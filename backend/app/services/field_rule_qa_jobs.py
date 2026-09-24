import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import uuid4


QA_JOB_TTL = timedelta(hours=1)
_jobs: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


def submit_qa_job(work: Callable[[], Awaitable[dict]]) -> dict[str, str]:
    _discard_expired_jobs()
    job_id = uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "PENDING",
        "result": None,
        "error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    task = asyncio.create_task(_run_job(job_id, work))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "PENDING"}


def get_qa_job(job_id: str) -> dict[str, Any] | None:
    _discard_expired_jobs()
    job = _jobs.get(job_id)
    if not job:
        return None
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }


async def _run_job(job_id: str, work: Callable[[], Awaitable[dict]]) -> None:
    job = _jobs[job_id]
    job["status"] = "PROCESSING"
    job["updated_at"] = datetime.now(UTC)
    try:
        job["result"] = await work()
        job["status"] = "COMPLETED"
    except Exception as exc:
        job["error"] = str(exc) or exc.__class__.__name__
        job["status"] = "FAILED"
    finally:
        job["updated_at"] = datetime.now(UTC)


def _discard_expired_jobs() -> None:
    cutoff = datetime.now(UTC) - QA_JOB_TTL
    expired = [job_id for job_id, job in _jobs.items() if job["updated_at"] < cutoff]
    for job_id in expired:
        _jobs.pop(job_id, None)


def _reset_qa_jobs_for_tests() -> None:
    for task in tuple(_tasks):
        task.cancel()
    _tasks.clear()
    _jobs.clear()
