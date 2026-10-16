"""In-memory work queue for the user's already-running Chrome extension."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4


@dataclass
class ChromeJob:
    id: str
    task_id: int
    task_name: str
    keyword: str
    max_pages: int
    personal_only: bool
    free_shipping: bool
    min_price: str | None
    max_price: str | None
    decision_mode: str = "ai"
    state: str = "pending"
    claimed_at: datetime | None = None

    def public_payload(self) -> dict:
        return {
            "job_id": self.id,
            "task_name": self.task_name,
            "keyword": self.keyword,
            "max_pages": self.max_pages,
            "personal_only": self.personal_only,
            "free_shipping": self.free_shipping,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "decision_mode": self.decision_mode,
        }


class ChromeTaskQueue:
    def __init__(self):
        self.jobs: dict[int, ChromeJob] = {}

    def enqueue(self, task) -> ChromeJob | None:
        if task.id in self.jobs:
            return None
        job = ChromeJob(
            id=uuid4().hex,
            task_id=task.id,
            task_name=task.task_name,
            keyword=task.keyword,
            max_pages=max(1, min(task.max_pages, 50)),
            personal_only=task.personal_only,
            free_shipping=task.free_shipping,
            min_price=task.min_price,
            max_price=task.max_price,
            decision_mode=getattr(task, "decision_mode", "ai"),
        )
        self.jobs[task.id] = job
        return job

    def claim_next(self) -> ChromeJob | None:
        now = datetime.now(timezone.utc)
        for job in self.jobs.values():
            if job.state == "claimed" and job.claimed_at and now - job.claimed_at > timedelta(minutes=10):
                job.state = "pending"
                job.claimed_at = None
            if job.state == "pending":
                job.state = "claimed"
                job.claimed_at = now
                return job
        return None

    def get(self, job_id: str) -> ChromeJob | None:
        return next((job for job in self.jobs.values() if job.id == job_id), None)

    def finish(self, job_id: str) -> ChromeJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        self.jobs.pop(job.task_id, None)
        return job

    def cancel(self, task_id: int) -> ChromeJob | None:
        return self.jobs.pop(task_id, None)

    def reindex_after_delete(self, task_id: int) -> None:
        updated = {}
        for old_id, job in self.jobs.items():
            if old_id == task_id:
                continue
            job.task_id = old_id - 1 if old_id > task_id else old_id
            updated[job.task_id] = job
        self.jobs = updated
