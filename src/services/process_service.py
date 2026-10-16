"""
进程管理服务
负责管理爬虫进程的启动和停止
"""

import asyncio
import contextlib
import os
import signal
import sys
from datetime import datetime
from typing import Awaitable, Callable, Dict, TextIO

from src.ai_handler import send_ntfy_notification
from src.config import STATE_FILE
from src.failure_guard import FailureGuard
from src.infrastructure.persistence.sqlite_task_repository import find_task_by_name_sync
from src.services.account_strategy_service import resolve_account_state_path
from src.services.chrome_task_queue import ChromeTaskQueue
from src.utils import build_task_log_path

STOP_TIMEOUT_SECONDS = 20
SPIDER_DEBUG_LIMIT_ENV = "SPIDER_DEBUG_LIMIT"
LifecycleHook = Callable[[int], Awaitable[None] | None]


class ProcessService:
    """进程管理服务"""

    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.log_paths: Dict[int, str] = {}
        self.log_handles: Dict[int, TextIO] = {}
        self.task_names: Dict[int, str] = {}
        self.exit_watchers: Dict[int, asyncio.Task] = {}
        self.failure_guard = FailureGuard()
        self.chrome_queue = ChromeTaskQueue()
        self.chrome_imports: dict[str, list[asyncio.Task]] = {}
        self.chrome_finalizers: dict[str, asyncio.Task] = {}
        self.chrome_import_errors: dict[str, str] = {}
        self._on_started: LifecycleHook | None = None
        self._on_stopped: LifecycleHook | None = None

    def set_lifecycle_hooks(
        self,
        *,
        on_started: LifecycleHook | None = None,
        on_stopped: LifecycleHook | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_stopped = on_stopped

    async def _invoke_hook(self, hook: LifecycleHook | None, task_id: int) -> None:
        if hook is None:
            return
        result = hook(task_id)
        if asyncio.iscoroutine(result):
            await result

    def _resolve_cookie_path(self, task_name: str) -> str | None:
        """Best-effort cookie/state path for a task."""
        try:
            task = find_task_by_name_sync(task_name)
            if task and isinstance(task.account_state_file, str) and task.account_state_file.strip():
                return resolve_account_state_path(
                    task.account_state_file,
                    state_dir=os.getenv("ACCOUNT_STATE_DIR", "state"),
                )
        except Exception:
            pass

        return STATE_FILE if os.path.exists(STATE_FILE) else None

    def is_running(self, task_id: int) -> bool:
        """检查任务是否正在运行"""
        if task_id in self.chrome_queue.jobs:
            return True
        process = self.processes.get(task_id)
        return process is not None and process.returncode is None

    async def _drain_finished_process(self, task_id: int) -> None:
        process = self.processes.get(task_id)
        if process is None or process.returncode is None:
            return

        watcher = self.exit_watchers.get(task_id)
        if watcher is not None:
            await asyncio.shield(watcher)
            return

        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _open_log_file(self, task_id: int, task_name: str) -> tuple[str, TextIO]:
        os.makedirs("logs", exist_ok=True)
        log_file_path = build_task_log_path(task_id, task_name)
        log_file_handle = open(log_file_path, "a", encoding="utf-8")
        return log_file_path, log_file_handle

    def _write_chrome_log(self, task_id: int, task_name: str, message: str) -> None:
        try:
            path, handle = self._open_log_file(task_id, task_name)
            with handle:
                handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except OSError as exc:
            print(f"写入 Chrome 任务日志失败: {exc}")

    def _build_spawn_command(self, task_name: str) -> list[str]:
        command = [
            sys.executable,
            "-u",
            "spider_v2.py",
            "--task-name",
            task_name,
        ]
        debug_limit = str(os.getenv(SPIDER_DEBUG_LIMIT_ENV, "")).strip()
        if debug_limit.isdigit() and int(debug_limit) > 0:
            command.extend(["--debug-limit", debug_limit])
        return command

    async def _spawn_process(
        self,
        task_name: str,
        log_file_handle: TextIO,
    ) -> asyncio.subprocess.Process:
        preexec_fn = os.setsid if sys.platform != "win32" else None
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        return await asyncio.create_subprocess_exec(
            *self._build_spawn_command(task_name),
            stdout=log_file_handle,
            stderr=log_file_handle,
            preexec_fn=preexec_fn,
            env=child_env,
        )

    def _register_runtime(
        self,
        task_id: int,
        task_name: str,
        process: asyncio.subprocess.Process,
        log_file_path: str,
        log_file_handle: TextIO,
    ) -> None:
        self.processes[task_id] = process
        self.log_paths[task_id] = log_file_path
        self.log_handles[task_id] = log_file_handle
        self.task_names[task_id] = task_name
        self.exit_watchers[task_id] = asyncio.create_task(self._watch_process_exit(process))

    async def start_task(self, task_id: int, task_name: str) -> bool:
        """启动任务进程"""
        await self._drain_finished_process(task_id)
        if self.is_running(task_id):
            print(f"任务 '{task_name}' (ID: {task_id}) 已在运行中")
            return False

        if os.getenv("CHROME_BROWSER_MODE", "").lower() == "existing":
            task = find_task_by_name_sync(task_name)
            if task is None or not task.enabled or task.id != task_id:
                return False
            if task.new_publish_option or task.region:
                print(f"任务 '{task_name}' 使用了现有 Chrome 模式尚不支持的筛选条件")
                return False
            job = self.chrome_queue.enqueue(task)
            self._write_chrome_log(
                task_id, task_name,
                f"=== 本次运行开始：现有 Chrome，任务 '{task_name}'，运行 ID={job.id} ===",
            )
            print(f"任务 '{task_name}' 已等待现有 Chrome 扩展执行")
            self._write_chrome_log(task_id, task_name, "已进入现有 Chrome 队列，等待扩展接取。")
            await self._invoke_hook(self._on_started, task_id)
            return True

        decision = self.failure_guard.should_skip_start(
            task_name,
            cookie_path=self._resolve_cookie_path(task_name),
        )
        if decision.skip:
            await self._notify_skip(task_name, decision)
            return False

        log_file_path = ""
        log_file_handle = None
        try:
            log_file_path, log_file_handle = self._open_log_file(task_id, task_name)
            log_file_handle.write(
                f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"=== 本次运行开始：独立爬虫进程，任务 '{task_name}' ===\n"
            )
            log_file_handle.flush()
            process = await self._spawn_process(task_name, log_file_handle)
        except Exception as exc:
            self._close_log_handle(log_file_handle)
            print(f"启动任务 '{task_name}' 失败: {exc}")
            return False

        self._register_runtime(task_id, task_name, process, log_file_path, log_file_handle)
        print(f"启动任务 '{task_name}' (PID: {process.pid})")
        await self._invoke_hook(self._on_started, task_id)
        return True

    async def _notify_skip(self, task_name: str, decision) -> None:
        print(
            f"[FailureGuard] 跳过启动任务 '{task_name}'，已暂停重试 "
            f"(连续失败 {decision.consecutive_failures}/{self.failure_guard.threshold})"
        )
        if not decision.should_notify:
            return
        try:
            await send_ntfy_notification(
                {
                    "商品标题": f"[任务暂停] {task_name}",
                    "当前售价": "N/A",
                    "商品链接": "#",
                },
                "任务处于暂停状态，将跳过执行。\n"
                f"原因: {decision.reason}\n"
                f"连续失败: {decision.consecutive_failures}/{self.failure_guard.threshold}\n"
                f"暂停到: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                "修复方法: 更新登录态/cookies文件后会自动恢复。",
            )
        except Exception as exc:
            print(f"发送任务暂停通知失败: {exc}")

    async def _watch_process_exit(self, process: asyncio.subprocess.Process) -> None:
        await process.wait()
        task_id = self._find_task_id_by_process(process)
        if task_id is None:
            return
        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _find_task_id_by_process(self, process: asyncio.subprocess.Process) -> int | None:
        for task_id, current_process in self.processes.items():
            if current_process is process:
                return task_id
        return None

    def _cleanup_runtime(
        self,
        task_id: int,
        process: asyncio.subprocess.Process,
    ) -> None:
        if self.processes.get(task_id) is not process:
            return
        self.processes.pop(task_id, None)
        self.log_paths.pop(task_id, None)
        self.task_names.pop(task_id, None)
        self._close_log_handle(self.log_handles.pop(task_id, None))
        self.exit_watchers.pop(task_id, None)

    def _close_log_handle(self, log_handle: TextIO | None) -> None:
        if log_handle is None:
            return
        with contextlib.suppress(Exception):
            log_handle.close()

    def _append_stop_marker(self, log_path: str | None, message: str = "!!! 任务已被终止 !!!") -> None:
        if not log_path:
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] {message}\n")
        except Exception as exc:
            print(f"写入任务终止标记失败: {exc}")

    async def stop_task(self, task_id: int, *, reason: str = "收到停止请求") -> bool:
        """停止任务进程"""
        chrome_job = self.chrome_queue.cancel(task_id)
        if chrome_job is not None:
            pending = self.chrome_imports.pop(chrome_job.id, [])
            finalizer = self.chrome_finalizers.pop(chrome_job.id, None)
            if finalizer is not None:
                pending = [*pending, finalizer]
            for work in pending:
                work.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self.chrome_import_errors.pop(chrome_job.id, None)
            self._write_chrome_log(task_id, chrome_job.task_name, f"任务已取消，原因：{reason}。")
            await self._invoke_hook(self._on_stopped, task_id)
            return True
        await self._drain_finished_process(task_id)
        process = self.processes.get(task_id)
        if process is None:
            print(f"任务 ID {task_id} 没有正在运行的进程")
            return False
        if process.returncode is not None:
            await self._await_exit_watcher(task_id)
            print(f"任务进程 {process.pid} (ID: {task_id}) 已退出，略过停止")
            return False

        log_path = self.log_paths.get(task_id)
        try:
            self._append_stop_marker(
                log_path,
                f"停止请求：{reason} (PID={process.pid}, SIGTERM)。",
            )
            await self._terminate_process(process, task_id)
            self._append_stop_marker(log_path)
            await self._await_exit_watcher(task_id)
            print(f"任务进程 {process.pid} (ID: {task_id}) 已终止")
            return True
        except ProcessLookupError:
            print(f"进程 (ID: {task_id}) 已不存在")
            return False
        except Exception as exc:
            print(f"停止任务进程 (ID: {task_id}) 时出错: {exc}")
            return False

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        task_id: int,
    ) -> None:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
            return
        except asyncio.TimeoutError:
            print(
                f"任务进程 {process.pid} (ID: {task_id}) 未在 "
                f"{STOP_TIMEOUT_SECONDS} 秒内退出，准备强制终止..."
            )

        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
        await process.wait()

    async def _await_exit_watcher(self, task_id: int) -> None:
        watcher = self.exit_watchers.get(task_id)
        if watcher is None:
            return
        await asyncio.shield(watcher)

    def reindex_after_delete(self, deleted_task_id: int) -> None:
        """删除任务后同步重排运行时索引，避免任务下标漂移。"""
        self.chrome_queue.reindex_after_delete(deleted_task_id)
        self.processes = self._reindex_mapping(self.processes, deleted_task_id)
        self.log_paths = self._reindex_mapping(self.log_paths, deleted_task_id)
        self.log_handles = self._reindex_mapping(self.log_handles, deleted_task_id)
        self.task_names = self._reindex_mapping(self.task_names, deleted_task_id)
        self.exit_watchers = self._reindex_mapping(self.exit_watchers, deleted_task_id)

    def _reindex_mapping(self, mapping: Dict[int, object], deleted_task_id: int) -> Dict[int, object]:
        reindexed: Dict[int, object] = {}
        for task_id, value in mapping.items():
            if task_id == deleted_task_id:
                continue
            next_task_id = task_id - 1 if task_id > deleted_task_id else task_id
            reindexed[next_task_id] = value
        return reindexed

    async def stop_all(self) -> None:
        """停止所有任务进程"""
        task_ids = list(set(self.processes) | set(self.chrome_queue.jobs))
        for task_id in task_ids:
            await self.stop_task(task_id, reason="Web 服务关闭或重启")

    def claim_chrome_job(self):
        job = self.chrome_queue.claim_next()
        if job is not None:
            self._write_chrome_log(job.task_id, job.task_name, "Chrome 扩展已接取任务，正在采集搜索页。")
        return job

    def submit_chrome_import(self, job_id: str, work: Callable[[], Awaitable[dict]]) -> None:
        job = self.chrome_queue.get(job_id)
        if job is None or job.state == "finalizing":
            raise ValueError("Chrome 任务已不存在、已取消或已结束采集")
        imports = self.chrome_imports.setdefault(job_id, [])
        previous = imports[-1] if imports else None
        batch = len(imports) + 1
        job.state = "processing"
        self._write_chrome_log(job.task_id, job.task_name, f"已接收第 {batch} 批商品，排队分析并保存。")

        async def run():
            if previous is not None:
                await asyncio.shield(previous)
            try:
                self._write_chrome_log(job.task_id, job.task_name, f"正在分析第 {batch} 批商品。")
                result = await work()
                self._write_chrome_log(
                    job.task_id, job.task_name,
                    f"第 {batch} 批处理完成：读取 {result['received']} 条，符合条件 {result['selected']} 条，保存 {result['saved']} 条。",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.chrome_import_errors[job_id] = str(exc)
                self._write_chrome_log(job.task_id, job.task_name, f"第 {batch} 批处理失败：{exc}")

        imports.append(asyncio.create_task(run()))

    async def finish_chrome_job(self, job_id: str, error: str | None = None) -> bool:
        job = self.chrome_queue.get(job_id)
        if job is None:
            return False
        if job_id in self.chrome_finalizers:
            return True
        imports = self.chrome_imports.get(job_id, [])
        if imports:
            job.state = "finalizing"
            self._write_chrome_log(job.task_id, job.task_name, "Chrome 页面采集已结束，后台继续分析并保存商品。")

            async def finalize():
                await asyncio.gather(*imports)
                await self._complete_chrome_job(job_id, error)

            finalizer = asyncio.create_task(finalize())
            self.chrome_finalizers[job_id] = finalizer
            finalizer.add_done_callback(lambda _done: self.chrome_finalizers.pop(job_id, None))
            return True
        return await self._complete_chrome_job(job_id, error)

    async def _complete_chrome_job(self, job_id: str, error: str | None = None) -> bool:
        job = self.chrome_queue.finish(job_id)
        if job is None:
            return False
        import_error = self.chrome_import_errors.pop(job_id, None)
        error = error or import_error
        self.chrome_imports.pop(job_id, None)
        result = f"执行失败：{error[:500]}" if error else "现有 Chrome 任务已完成。"
        self._write_chrome_log(job.task_id, job.task_name, result)
        await self._invoke_hook(self._on_stopped, job.task_id)
        return True
