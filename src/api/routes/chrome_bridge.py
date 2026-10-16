"""Local import endpoint for cards captured in the user's existing Chrome session."""

import asyncio

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.infrastructure.persistence.sqlite_task_repository import find_task_by_name_sync
from src.api.dependencies import get_process_service
from fastapi import Depends
from src.services.process_service import ProcessService
from src.services.chrome_browser_import import (
    ingest_search_cards,
    normalize_search_cards,
    valid_bridge_token,
)


router = APIRouter(prefix="/api/chrome-bridge", tags=["chrome-bridge"])


class SearchImport(BaseModel):
    task_name: str = Field(min_length=1, max_length=100)
    page_url: str = Field(min_length=1, max_length=2048)
    cards: list[dict] = Field(min_length=1, max_length=50)
    job_id: str | None = None


def _require_token(token: str) -> None:
    if not valid_bridge_token(token):
        raise HTTPException(status_code=403, detail="Chrome bridge 未配对")


@router.get("/next")
async def next_job(
    x_chrome_bridge_token: str = Header(default=""),
    process_service: ProcessService = Depends(get_process_service),
):
    _require_token(x_chrome_bridge_token)
    job = process_service.claim_chrome_job()
    return {"job": job.public_payload() if job else None}


class JobComplete(BaseModel):
    job_id: str
    error: str | None = None


@router.post("/complete")
async def complete_job(
    payload: JobComplete,
    x_chrome_bridge_token: str = Header(default=""),
    process_service: ProcessService = Depends(get_process_service),
):
    _require_token(x_chrome_bridge_token)
    if not await process_service.finish_chrome_job(payload.job_id, payload.error):
        raise HTTPException(status_code=404, detail="任务已不存在或已取消")
    if payload.error:
        print(f"现有 Chrome 执行失败: {payload.error[:500]}")
    return {"accepted": True, "completed": process_service.chrome_queue.get(payload.job_id) is None}


@router.post("/import")
async def import_search(
    payload: SearchImport,
    x_chrome_bridge_token: str = Header(default=""),
    process_service: ProcessService = Depends(get_process_service),
):
    _require_token(x_chrome_bridge_token)
    task = await asyncio.to_thread(find_task_by_name_sync, payload.task_name)
    if task is None or not task.enabled:
        raise HTTPException(status_code=404, detail="未找到已启用的任务")
    if payload.job_id:
        job = process_service.chrome_queue.get(payload.job_id)
        if job is None or job.task_name != task.task_name or job.state not in {"claimed", "processing"}:
            raise HTTPException(status_code=404, detail="Chrome 任务已不存在或已取消")
    try:
        cards = normalize_search_cards(
            page_url=payload.page_url,
            expected_keyword=task.keyword,
            cards=payload.cards,
        )
        if payload.job_id:
            process_service.submit_chrome_import(
                payload.job_id, lambda: ingest_search_cards(task, cards, run_id=payload.job_id)
            )
            return JSONResponse(status_code=202, content={
                "accepted": True, "task_name": task.task_name,
                "received": len(cards), "processing": True,
            })
        result = await ingest_search_cards(task, cards)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_name": task.task_name, **result}
