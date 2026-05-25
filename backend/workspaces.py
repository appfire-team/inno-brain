"""Workspaces — isolated knowledge graphs.

Each workspace owns its own corpus, graph, conversations, and ForeSight
sessions, stored under:

    data/workspaces/<id>/
        workspace.json          # metadata (name, created_at, source_workspace_id)
        raw/                    # uploaded source docs
        graphify-out/
            graph.json
            insights.json
        conversations/          # one JSON per thread
        foresight/              # one JSON per session

Global (shared across workspaces):
    data/memory.json            # persistent memory
    data/rubrics/               # evaluation rubrics
    data/foresight_personas/    # reusable persona library

A `Workspace` object is a thin path container plus a per-workspace pipeline
lock. Pass an instance through to any function that touches workspace data.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
WS_ROOT = DATA_DIR / "workspaces"

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class Workspace:
    """Path container for one workspace. Cheap to construct; not cached."""

    def __init__(self, ws_id: str, meta: dict[str, Any]) -> None:
        self.id = ws_id
        self.name: str = meta.get("name") or ws_id
        self.created_at: float | None = meta.get("created_at")
        self.updated_at: float | None = meta.get("updated_at")
        self.source_workspace_id: str | None = meta.get("source_workspace_id")

    @property
    def path(self) -> Path:
        return WS_ROOT / self.id

    @property
    def raw_dir(self) -> Path:
        return self.path / "raw"

    @property
    def out_dir(self) -> Path:
        return self.path / "graphify-out"

    @property
    def graph_file(self) -> Path:
        return self.out_dir / "graph.json"

    @property
    def insights_file(self) -> Path:
        return self.out_dir / "insights.json"

    @property
    def conv_dir(self) -> Path:
        return self.path / "conversations"

    @property
    def foresight_dir(self) -> Path:
        return self.path / "foresight"

    @property
    def artifacts_dir(self) -> Path:
        return self.path / "artifacts"

    @property
    def playbook_runs_dir(self) -> Path:
        return self.path / "playbook_runs"

    @property
    def memory_file(self) -> Path:
        return self.path / "memory.json"

    @property
    def rubrics_dir(self) -> Path:
        return self.path / "rubrics"

    @property
    def meta_file(self) -> Path:
        return self.path / "workspace.json"

    @property
    def pipeline_lock(self) -> threading.Lock:
        with _LOCKS_GUARD:
            lock = _LOCKS.get(self.id)
            if lock is None:
                lock = _LOCKS[self.id] = threading.Lock()
            return lock

    def ensure_dirs(self) -> None:
        for d in (
            self.raw_dir, self.out_dir, self.conv_dir, self.foresight_dir,
            self.artifacts_dir, self.playbook_runs_dir, self.rubrics_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def summary(self) -> dict[str, Any]:
        # Documents = top-level files in raw/; repos = subdirs (ingested
        # codebases). Counted separately because the dropdown row should
        # surface a repo-only workspace as "1 repo" not "0 docs".
        n_raw = 0
        n_repo = 0
        if self.raw_dir.exists():
            for p in self.raw_dir.iterdir():
                if p.name.startswith("."):
                    continue
                if p.is_file():
                    n_raw += 1
                elif p.is_dir():
                    n_repo += 1
        n_conv = sum(1 for _ in self.conv_dir.glob("*.json")) if self.conv_dir.exists() else 0
        n_fs = sum(1 for _ in self.foresight_dir.glob("*.json")) if self.foresight_dir.exists() else 0
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_workspace_id": self.source_workspace_id,
            "stats": {
                "documents": n_raw,
                "repos": n_repo,
                "conversations": n_conv,
                "foresight_sessions": n_fs,
                "has_graph": self.graph_file.exists(),
            },
        }


# ---------- ID / metadata helpers ------------------------------------------


def _safe_id(ws_id: str) -> str:
    safe = "".join(c for c in ws_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("Invalid workspace id")
    return safe


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _load_meta(ws_path: Path) -> dict[str, Any]:
    meta_file = ws_path / "workspace.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text())
        except json.JSONDecodeError:
            pass
    mtime = ws_path.stat().st_mtime
    return {"name": ws_path.name, "created_at": mtime, "updated_at": mtime}


def _save_meta(ws: Workspace) -> None:
    ws.ensure_dirs()
    ws.meta_file.write_text(json.dumps({
        "id": ws.id,
        "name": ws.name,
        "created_at": ws.created_at,
        "updated_at": ws.updated_at,
        "source_workspace_id": ws.source_workspace_id,
    }, indent=2))


# ---------- CRUD ------------------------------------------------------------


def list_workspaces() -> list[dict[str, Any]]:
    WS_ROOT.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(WS_ROOT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        ws = Workspace(p.name, _load_meta(p))
        out.append(ws.summary())
    return out


def get_workspace(ws_id: str) -> Workspace | None:
    sid = _safe_id(ws_id)
    p = WS_ROOT / sid
    if not p.is_dir():
        return None
    return Workspace(sid, _load_meta(p))


def create_workspace(
    name: str,
    source_workspace_id: str | None = None,
    *,
    _id: str | None = None,
    seed_rubrics: list[dict[str, Any]] | None = None,
) -> Workspace:
    """Create a new workspace. If `source_workspace_id` is given, deep-copy the
    source workspace's raw docs + graph + insights. Conversations and foresight
    sessions are NOT copied (they belong to the source analysis).

    `seed_rubrics` is an optional list of rubric snapshots (each {id, name, body})
    to write into the new workspace's rubrics dir. Used by the create-workspace
    UI to let the user pick an existing rubric to copy.
    """
    WS_ROOT.mkdir(parents=True, exist_ok=True)
    ws_id = _safe_id(_id) if _id else _new_id()
    p = WS_ROOT / ws_id
    if p.exists():
        raise ValueError(f"Workspace already exists: {ws_id}")

    now = time.time()
    ws = Workspace(ws_id, {
        "name": name.strip() or "Untitled",
        "created_at": now,
        "updated_at": now,
        "source_workspace_id": source_workspace_id,
    })
    p.mkdir(parents=True)
    ws.ensure_dirs()

    if source_workspace_id:
        src = get_workspace(source_workspace_id)
        if src is None:
            shutil.rmtree(p, ignore_errors=True)
            raise ValueError(f"Source workspace not found: {source_workspace_id}")
        if src.raw_dir.exists():
            shutil.rmtree(ws.raw_dir, ignore_errors=True)
            shutil.copytree(src.raw_dir, ws.raw_dir)
        if src.out_dir.exists():
            shutil.rmtree(ws.out_dir, ignore_errors=True)
            shutil.copytree(src.out_dir, ws.out_dir)

    if seed_rubrics:
        for r in seed_rubrics:
            rid = r.get("id")
            body = r.get("body")
            rname = r.get("name") or "Untitled rubric"
            if not rid or body is None:
                continue
            safe = "".join(c for c in str(rid) if c.isalnum() or c in "-_")
            if not safe:
                continue
            (ws.rubrics_dir / f"{safe}.json").write_text(json.dumps({
                "id": safe,
                "name": rname,
                "body": body,
                "created_at": now,
                "updated_at": now,
            }, indent=2))

    _save_meta(ws)
    return ws


def rename_workspace(ws_id: str, name: str) -> Workspace | None:
    ws = get_workspace(ws_id)
    if ws is None:
        return None
    new_name = name.strip()
    if new_name:
        ws.name = new_name
    ws.updated_at = time.time()
    _save_meta(ws)
    return ws


def delete_workspace(ws_id: str) -> bool:
    listing = list_workspaces()
    if len(listing) <= 1:
        raise ValueError("Cannot delete the last workspace")
    sid = _safe_id(ws_id)
    p = WS_ROOT / sid
    if not p.is_dir():
        return False
    shutil.rmtree(p)
    with _LOCKS_GUARD:
        _LOCKS.pop(sid, None)
    return True


# ---------- Migration -------------------------------------------------------


def _legacy_dirs() -> list[tuple[Path, str]]:
    return [
        (DATA_DIR / "raw", "raw"),
        (DATA_DIR / "graphify-out", "graphify-out"),
        (DATA_DIR / "conversations", "conversations"),
        (DATA_DIR / "foresight", "foresight"),
    ]


def _has_legacy_content() -> bool:
    for src, _ in _legacy_dirs():
        if src.exists() and any(src.iterdir()):
            return True
    return False


def _migrate_global_memory_rubrics(default: Workspace) -> None:
    """Move legacy global data/memory.json + data/rubrics/ into the Default
    workspace. One-shot; called from ensure_default_workspace. Idempotent — if
    Default already has a memory.json or rubrics dir, the legacy files are
    left alone (caller can inspect + decide). Otherwise, moves them in."""
    legacy_mem = DATA_DIR / "memory.json"
    if legacy_mem.exists() and not default.memory_file.exists():
        shutil.move(str(legacy_mem), str(default.memory_file))

    legacy_rub = DATA_DIR / "rubrics"
    if legacy_rub.is_dir() and any(legacy_rub.glob("*.json")):
        default.rubrics_dir.mkdir(parents=True, exist_ok=True)
        existing_in_default = {p.name for p in default.rubrics_dir.glob("*.json")}
        for src in legacy_rub.glob("*.json"):
            if src.name in existing_in_default:
                continue
            shutil.move(str(src), str(default.rubrics_dir / src.name))
        # Remove the legacy rubrics dir if it's now empty.
        try:
            if not any(legacy_rub.iterdir()):
                legacy_rub.rmdir()
        except OSError:
            pass


def ensure_default_workspace() -> Workspace:
    """Startup hook. If workspaces/ is empty:
      - If legacy data exists at data/{raw,graphify-out,conversations,foresight}/,
        create a 'default' workspace and MOVE the legacy data into it.
      - Otherwise, create an empty 'default' workspace.
    If workspaces/ already has entries, return the most recently updated one.
    Either way, sweep any legacy global memory.json + rubrics/ into Default
    (memory and rubrics were promoted to per-workspace storage).
    """
    WS_ROOT.mkdir(parents=True, exist_ok=True)
    existing = [p for p in WS_ROOT.iterdir() if p.is_dir()]
    if existing:
        listing = list_workspaces()
        latest = get_workspace(listing[0]["id"])  # type: ignore[assignment]
        default = get_workspace("default") or latest
        if default is not None:
            _migrate_global_memory_rubrics(default)
        return latest  # type: ignore[return-value]

    has_legacy = _has_legacy_content()
    default = create_workspace("Default", _id="default")

    if has_legacy:
        for src, dst_name in _legacy_dirs():
            if not src.exists():
                continue
            dst = default.path / dst_name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
        default.ensure_dirs()  # recreate any subdirs that didn't exist in legacy

    _migrate_global_memory_rubrics(default)
    return default
