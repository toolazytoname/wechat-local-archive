"""Local JSON job store for export and guided-read workflows. No Redis."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from wechat_export.fsutil import write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    job_id: str
    kind: str
    state: str
    created_at: str
    updated_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, str] | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payload": self.payload,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
        }


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def _path(self, job_id: str) -> Path:
        if "/" in job_id or job_id in {".", ".."}:
            raise ValueError("invalid job id")
        return self.root / f"{job_id}.json"

    def create(self, kind: str, state: str, payload: dict[str, Any] | None = None) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            kind=kind,
            state=state,
            created_at=_now(),
            updated_at=_now(),
            payload=payload or {},
        )
        with self._lock:
            write_json(self._path(job.job_id), job.to_dict())
            self._events[job.job_id] = threading.Event()
        return job

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Job(
            job_id=raw["job_id"],
            kind=raw["kind"],
            state=raw["state"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            payload=raw.get("payload") or {},
            error=raw.get("error"),
            cancel_requested=bool(raw.get("cancel_requested")),
        )

    def save(self, job: Job) -> None:
        job.updated_at = _now()
        with self._lock:
            existing = self.get(job.job_id)
            if existing and existing.cancel_requested:
                job.cancel_requested = True
                job.state = "cancelled"
            write_json(self._path(job.job_id), job.to_dict())

    def request_cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if not job:
            return None
        job.cancel_requested = True
        if job.state not in {"ready", "failed", "cancelled"}:
            job.state = "cancelled"
        self.save(job)
        event = self._events.get(job_id)
        if event:
            event.set()
        return job

    def cancelled(self, job_id: str) -> bool:
        event = self._events.get(job_id)
        if event and event.is_set():
            return True
        job = self.get(job_id)
        return bool(job and job.cancel_requested)

    def busy(self, job_id: str) -> bool:
        import fcntl
        with (self.root / (job_id + ".lock")).open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True

    def run_in_thread(self, job_id: str, fn: Callable[[], None]) -> bool:
        import fcntl
        with self._lock:
            existing = self._threads.get(job_id)
            if existing and existing.is_alive():
                return False
            lease = (self.root / (job_id + ".lock")).open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close()
                return False
            def guarded():
                try:
                    fn()
                except Exception:
                    job = self.get(job_id)
                    if job:
                        job.state = "failed"
                        job.error = {"code": "worker_failed", "message": "Local job failed; no sensitive error details exposed."}
                        self.save(job)
                finally:
                    lease.close()
            thread = threading.Thread(target=guarded, name=f"job-{job_id[:8]}", daemon=True)
            self._threads[job_id] = thread
            thread.start()
            return True

    def recover_interrupted(self) -> int:
        """Only stale active jobs whose cross-process lease is free are changed."""
        import fcntl
        active = {"snapshotting", "preparing_reader", "acquiring_key", "decrypting",
                  "normalizing", "indexing", "exporting", "validating", "running", "queued"}
        count = 0
        for path in self.root.glob("*.json"):
            try:
                job = self.get(path.stem)
                if not job or job.state not in active:
                    continue
                with (self.root / (job.job_id + ".lock")).open("a") as lease:
                    try:
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    job.state = "blocked"
                    job.error = {"code": "interrupted", "message": "Previous process stopped. Originals were not restored. Create a new job; do not reuse a live grant."}
                    job.payload.pop("live_grant", None)
                    self.save(job)
                    count += 1
            except (OSError, ValueError, KeyError):
                continue
        return count
