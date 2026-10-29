"""In-memory tracker for scrape job progress, polled by the UI's progress bar.
Not persisted — fine for a single-process app, and jobs are short-lived."""

import threading
import time
import uuid

_JOB_TTL_SECONDS = 3600

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def create_job(venue: str) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _prune_old_jobs()
        _jobs[job_id] = {
            "venue": venue,
            "status": "running",
            "percent": 0,
            "message": "Starting…",
            "created_at": time.time(),
        }
    return job_id


def update_job(job_id: str, percent: int, message: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["percent"] = percent
            job["message"] = message


def finish_job(job_id: str, message: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["status"] = "done"
            job["percent"] = 100
            job["message"] = message


def fail_job(job_id: str, message: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["status"] = "error"
            job["message"] = message


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _prune_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    for jid in [jid for jid, j in _jobs.items() if j["created_at"] < cutoff]:
        del _jobs[jid]
