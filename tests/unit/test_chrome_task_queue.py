import asyncio
from types import SimpleNamespace

from src.services.chrome_task_queue import ChromeTaskQueue
from src.services.process_service import ProcessService


def _task(task_id=1):
    return SimpleNamespace(
        id=task_id, task_name="iPad air", enabled=True, keyword="iPad air",
        max_pages=3, personal_only=True, free_shipping=True,
        min_price="500", max_price="2000", new_publish_option=None, region=None,
    )


def test_chrome_queue_claim_finish_and_cancel():
    queue = ChromeTaskQueue()
    job = queue.enqueue(_task())
    assert job.public_payload()["keyword"] == "iPad air"
    assert queue.enqueue(_task()) is None
    assert queue.claim_next() is job
    assert queue.claim_next() is None
    assert queue.finish(job.id) is job
    assert queue.claim_next() is None
    assert queue.cancel(1) is None


def test_process_service_existing_chrome_mode_never_spawns(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_BROWSER_MODE", "existing")
    monkeypatch.setattr("src.services.process_service.find_task_by_name_sync", lambda _: _task())
    monkeypatch.setattr(
        "src.services.process_service.build_task_log_path",
        lambda task_id, _task_name: str(tmp_path / f"task-{task_id}.log"),
    )
    service = ProcessService()
    events = []
    service.set_lifecycle_hooks(
        on_started=lambda task_id: events.append(("start", task_id)),
        on_stopped=lambda task_id: events.append(("stop", task_id)),
    )

    async def forbidden_spawn(*_args):
        raise AssertionError("existing Chrome mode must not launch Playwright")

    service._spawn_process = forbidden_spawn

    async def scenario():
        assert await service.start_task(1, "iPad air")
        assert service.is_running(1)
        job = service.chrome_queue.claim_next()
        assert job is not None
        assert await service.finish_chrome_job(job.id)
        assert not service.is_running(1)

    asyncio.run(scenario())
    assert events == [("start", 1), ("stop", 1)]
    assert "等待扩展接取" in (tmp_path / "task-1.log").read_text(encoding="utf-8")
    assert "本次运行开始" in (tmp_path / "task-1.log").read_text(encoding="utf-8")


def test_chrome_capture_completion_waits_for_background_analysis(monkeypatch):
    async def scenario():
        service = ProcessService()
        monkeypatch.setattr(service, "_write_chrome_log", lambda *_args: None)
        job = service.chrome_queue.enqueue(_task())
        service.chrome_queue.claim_next()
        entered, release = asyncio.Event(), asyncio.Event()
        stopped = []
        service.set_lifecycle_hooks(on_stopped=lambda task_id: stopped.append(task_id))

        async def analyze():
            entered.set()
            await release.wait()
            return {"received": 1, "selected": 1, "saved": 1}

        service.submit_chrome_import(job.id, analyze)
        await entered.wait()
        assert await service.finish_chrome_job(job.id)
        finalizer = service.chrome_finalizers[job.id]
        assert service.is_running(1)
        assert job.state == "finalizing"
        assert service.chrome_queue.claim_next() is None
        release.set()
        await finalizer
        assert not service.is_running(1)
        assert stopped == [1]

    asyncio.run(scenario())


def test_stopping_chrome_job_cancels_its_analysis(monkeypatch):
    async def scenario():
        service = ProcessService()
        monkeypatch.setattr(service, "_write_chrome_log", lambda *_args: None)
        job = service.chrome_queue.enqueue(_task())
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def analyze():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        service.submit_chrome_import(job.id, analyze)
        await entered.wait()
        assert await service.stop_task(1)
        assert cancelled.is_set()
        assert not service.is_running(1)
        assert job.id not in service.chrome_imports

    asyncio.run(scenario())


def test_chrome_page_batches_analyze_in_order(monkeypatch):
    async def scenario():
        service = ProcessService()
        monkeypatch.setattr(service, "_write_chrome_log", lambda *_args: None)
        job = service.chrome_queue.enqueue(_task())
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def first():
            entered.set()
            await release.wait()
            order.append(1)
            return {"received": 1, "selected": 1, "saved": 1}

        async def second():
            order.append(2)
            return {"received": 1, "selected": 1, "saved": 1}

        service.submit_chrome_import(job.id, first)
        service.submit_chrome_import(job.id, second)
        await entered.wait()
        assert order == []
        release.set()
        await service.finish_chrome_job(job.id)
        await service.chrome_finalizers[job.id]
        assert order == [1, 2]

    asyncio.run(scenario())
