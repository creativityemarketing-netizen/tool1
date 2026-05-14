import time
import uuid
from typing import Any

# { job_id: { status, videos, progress, created_at, error } }
_jobs: dict[str, dict[str, Any]] = {}

JOB_TTL = 1800  # 30 minutes


def create_job() -> str:
    _purge_old()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "videos": [],
        "progress": {"fetched": 0, "total": 0, "title": ""},
        "error": None,
        "created_at": time.time(),
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def update_progress(job_id: str, fetched: int, total: int, title: str = "") -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["progress"] = {"fetched": fetched, "total": total, "title": title}


def complete_job(job_id: str, videos: list) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["videos"] = videos


def fail_job(job_id: str, error: str) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = error


def delete_job(job_id: str) -> None:
    _jobs.pop(job_id, None)


def _purge_old() -> None:
    now = time.time()
    expired = [jid for jid, j in _jobs.items() if now - j["created_at"] > JOB_TTL]
    for jid in expired:
        _jobs.pop(jid, None)
