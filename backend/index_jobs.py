"""Background indexing jobs — one per workspace at a time.

Long-running pipeline operations (ingest_repo, rebuild_graph) hold FastAPI
worker threads for many minutes when the corpus is large; the ngrok tunnel
and impatient HTTP clients time out long before the rebuild finishes, and
worse, every other request on the worker pool stalls because the threads
are blocked inside Claude API calls.

This module fixes that. Each long op runs on its own daemon thread; the API
handler returns immediately with a job descriptor. The frontend polls
GET /api/index-job to track status and surface a banner.

Constraints:
- Exactly one active indexing job per workspace (enforced via _lock).
- One global job per workspace persists in memory until the next start
  (lets the UI see the just-completed status long enough to show a toast).
- Workers can update a short `message` string for human-readable progress.
- We never persist jobs to disk — a backend restart loses in-flight job state,
  which is intentional: the next /api/stats call will reveal whether the
  graph rebuilt successfully.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable


# workspace_id → job dict (active or most-recent). One slot per workspace.
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _public(job: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with the keys safe to expose over HTTP. Keeps callers
    from accidentally mutating the in-memory record."""
    return {k: v for k, v in job.items() if k != "_thread"}


def get_job(workspace_id: str) -> dict[str, Any] | None:
    """Return the active or most-recently-finished job for a workspace."""
    with _lock:
        j = _jobs.get(workspace_id)
        return _public(j) if j else None


def is_busy(workspace_id: str) -> bool:
    """Lightweight check used by /api/stats and other read-mostly endpoints
    so they can warn the UI without paying for a full job lookup."""
    with _lock:
        j = _jobs.get(workspace_id)
        return bool(j and j.get("status") == "running")


def set_message(workspace_id: str, message: str) -> None:
    """Worker-side progress update. Safe no-op if no active job."""
    with _lock:
        j = _jobs.get(workspace_id)
        if j and j.get("status") == "running":
            j["message"] = message


def start(
    workspace_id: str,
    *,
    kind: str,
    label: str,
    fn: Callable[..., Any],
    args: tuple = (),
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Kick off a background job. Raises ValueError if one is already running.

    `fn` is invoked with the given args/kwargs on a daemon thread; its return
    value (or raised exception) becomes the job's `result` / `error`.
    """
    kwargs = kwargs or {}
    with _lock:
        existing = _jobs.get(workspace_id)
        if existing and existing.get("status") == "running":
            raise ValueError(
                f"An indexing job is already running for this workspace: "
                f"{existing.get('label') or existing.get('kind')}"
            )
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "workspace_id": workspace_id,
            "kind": kind,
            "label": label,
            "status": "running",
            "message": "Starting…",
            "started_at": _now(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        _jobs[workspace_id] = job

    def _run() -> None:
        try:
            result = fn(*args, **kwargs)
            with _lock:
                cur = _jobs.get(workspace_id)
                if cur and cur["id"] == job["id"]:
                    cur["status"] = "complete"
                    cur["message"] = "Done"
                    cur["result"] = (
                        result if isinstance(result, dict) else {"value": result}
                    )
                    cur["finished_at"] = _now()
        except Exception as exc:  # noqa: BLE001 — must never crash silently
            tb = traceback.format_exc()
            print(f"[index_jobs] {workspace_id} {kind} failed:\n{tb}", flush=True)
            with _lock:
                cur = _jobs.get(workspace_id)
                if cur and cur["id"] == job["id"]:
                    cur["status"] = "failed"
                    cur["error"] = str(exc)
                    cur["message"] = f"Failed: {exc}"
                    cur["finished_at"] = _now()

    t = threading.Thread(target=_run, daemon=True, name=f"index-job-{job['id']}")
    job["_thread"] = t  # internal; not exposed via _public
    t.start()
    return _public(job)
