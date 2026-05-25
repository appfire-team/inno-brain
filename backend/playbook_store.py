"""User-defined playbooks — workspace-scoped or global.

Built-in playbooks stay in `playbooks.PLAYBOOKS` (hardcoded). User playbooks
live as JSON files. Lookup order at runtime: built-in > workspace > global.

Schema mirrors the built-in PLAYBOOKS dict entries:
  {
    "id": str,
    "label": str,
    "tagline": str,
    "expected_duration_s": int,
    "accepts_source_types": [str, ...],
    "artifact_type": str,
    "steps": [step_def, ...],
    "scope": "workspace" | "global",
    "created_at": float,
    "updated_at": float,
  }

Validation:
- id must be safe (alnum + _-).
- steps[-1].type must be "synthesize" — every playbook produces an artifact.
- steps[-1].sections must be non-empty.
- intent_turn steps must reference an intent that exists (built-in or user).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from workspaces import Workspace

GLOBAL_DIR = Path(__file__).parent / "data" / "global_playbooks"
GLOBAL_DIR.mkdir(parents=True, exist_ok=True)

VALID_STEP_TYPES = {"intent_turn", "foresight", "simulate", "factcheck", "synthesize"}


def _safe_id(pid: str) -> str:
    safe = "".join(c for c in pid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid playbook id")
    return safe


def _workspace_dir(ws: Workspace) -> Path:
    ws.ensure_dirs()
    d = ws.path / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ws: Workspace | None, pid: str, scope: str) -> Path:
    if scope == "global":
        return GLOBAL_DIR / f"{_safe_id(pid)}.json"
    if ws is None:
        raise ValueError("workspace required for scope=workspace")
    return _workspace_dir(ws) / f"{_safe_id(pid)}.json"


def _save(ws: Workspace | None, pb: dict[str, Any]) -> None:
    _path(ws, pb["id"], pb["scope"]).write_text(json.dumps(pb, indent=2))


def _validate(pb: dict[str, Any], ws: Workspace) -> None:
    if not pb.get("label", "").strip():
        raise ValueError("label is required")
    if pb.get("artifact_type") not in _allowed_artifact_types():
        raise ValueError(f"unknown artifact_type: {pb.get('artifact_type')}")
    steps = pb.get("steps") or []
    if not steps:
        raise ValueError("at least one step is required")
    if steps[-1].get("type") != "synthesize":
        raise ValueError("last step must be of type 'synthesize'")
    for idx, s in enumerate(steps):
        if s.get("type") not in VALID_STEP_TYPES:
            raise ValueError(f"step {idx}: invalid type '{s.get('type')}'")
        if not re.match(r"^[a-z0-9_]+$", s.get("id", "")):
            raise ValueError(f"step {idx}: id must be lowercase letters/digits/_")
        if not s.get("label", "").strip():
            raise ValueError(f"step {idx}: label required")
        if s["type"] == "intent_turn":
            intent_id = s.get("intent") or ""
            if not _intent_exists(ws, intent_id):
                raise ValueError(f"step {idx}: unknown intent '{intent_id}'")
        elif s["type"] == "foresight":
            if not s.get("personas"):
                raise ValueError(f"step {idx}: foresight needs at least one persona")
        elif s["type"] == "simulate":
            if not s.get("personas"):
                raise ValueError(f"step {idx}: simulate needs at least one persona")
        elif s["type"] == "synthesize":
            if not s.get("sections"):
                raise ValueError(f"step {idx}: synthesize needs at least one section")


def _allowed_artifact_types() -> set[str]:
    # Imported lazily to avoid circular dependency at module load.
    import artifacts
    return set(artifacts.ARTIFACT_TYPES.keys())


def _intent_exists(ws: Workspace, intent_id: str) -> bool:
    from rubrics import INTENT_LABELS
    if intent_id in INTENT_LABELS:
        return True
    import intent_store
    return intent_store.get_intent(ws, intent_id) is not None


def list_playbooks(ws: Workspace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    ws_dir = ws.path / "playbooks"
    if ws_dir.exists():
        for p in sorted(ws_dir.glob("*.json")):
            try:
                pb = json.loads(p.read_text())
                pb["scope"] = "workspace"
                seen.add(pb["id"])
                out.append(pb)
            except json.JSONDecodeError:
                continue
    for p in sorted(GLOBAL_DIR.glob("*.json")):
        try:
            pb = json.loads(p.read_text())
            if pb["id"] in seen:
                continue
            pb["scope"] = "global"
            out.append(pb)
        except json.JSONDecodeError:
            continue
    return out


def get_playbook(ws: Workspace, pid: str) -> dict[str, Any] | None:
    pid = _safe_id(pid)
    ws_path = _workspace_dir(ws) / f"{pid}.json"
    if ws_path.exists():
        pb = json.loads(ws_path.read_text())
        pb["scope"] = "workspace"
        return pb
    gpath = GLOBAL_DIR / f"{pid}.json"
    if gpath.exists():
        pb = json.loads(gpath.read_text())
        pb["scope"] = "global"
        return pb
    return None


def create_playbook(ws: Workspace, payload: dict[str, Any]) -> dict[str, Any]:
    scope = payload.get("scope", "workspace")
    if scope not in ("workspace", "global"):
        raise ValueError("scope must be workspace or global")
    pid = _safe_id(payload.get("id", ""))
    # No collisions with built-ins or existing user playbooks.
    from playbooks import PLAYBOOKS
    if pid in PLAYBOOKS:
        raise ValueError(f"id collides with built-in playbook: {pid}")
    if get_playbook(ws, pid):
        raise ValueError(f"playbook already exists: {pid}")
    now = time.time()
    pb = {
        "id": pid,
        "label": (payload.get("label") or "").strip(),
        "tagline": (payload.get("tagline") or "").strip(),
        "expected_duration_s": int(payload.get("expected_duration_s") or 240),
        "accepts_source_types": list(payload.get("accepts_source_types") or []),
        "artifact_type": payload.get("artifact_type") or "StrategyBrief",
        "steps": list(payload.get("steps") or []),
        "scope": scope,
        "created_at": now,
        "updated_at": now,
    }
    _validate(pb, ws)
    target_ws = ws if scope == "workspace" else None
    _save(target_ws, pb)
    return pb


def update_playbook(ws: Workspace, pid: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Patch a user playbook — or materialize an override against a built-in id.

    First edit against a built-in pid creates a workspace-scoped override
    seeded from the canonical PLAYBOOKS entry, then applies the patch."""
    existing = get_playbook(ws, pid)
    if not existing:
        from playbooks import PLAYBOOKS
        if pid in PLAYBOOKS:
            base = PLAYBOOKS[pid]
            now = time.time()
            existing = {
                "id": pid,
                "label": base.get("label", pid),
                "tagline": base.get("tagline", ""),
                "expected_duration_s": base.get("expected_duration_s", 240),
                "accepts_source_types": list(base.get("accepts_source_types") or []),
                "artifact_type": base.get("artifact_type"),
                # Deep-copy steps so future edits don't mutate the built-in registry.
                "steps": json.loads(json.dumps(base.get("steps", []))),
                "scope": "workspace",
                "created_at": now,
                "updated_at": now,
            }
        else:
            return None
    for k in ("label", "tagline", "expected_duration_s", "accepts_source_types",
              "artifact_type", "steps"):
        if k in payload and payload[k] is not None:
            existing[k] = payload[k]
    existing["updated_at"] = time.time()
    _validate(existing, ws)
    target_ws = ws if existing["scope"] == "workspace" else None
    _save(target_ws, existing)
    return existing


def delete_playbook(ws: Workspace, pid: str) -> bool:
    """Delete a user playbook — or for a built-in pid, remove the override
    (which restores the built-in)."""
    existing = get_playbook(ws, pid)
    if not existing:
        return False
    target_ws = ws if existing["scope"] == "workspace" else None
    _path(target_ws, pid, existing["scope"]).unlink()
    return True


def restore_builtin_playbook(ws: Workspace, pid: str) -> bool:
    """Drop an override for a built-in playbook id. Returns False if pid is
    not a built-in or no override existed."""
    from playbooks import PLAYBOOKS
    if pid not in PLAYBOOKS:
        return False
    return delete_playbook(ws, pid)


def clone_playbook(
    ws: Workspace,
    source_id: str,
    *,
    new_id: str | None = None,
    scope: str = "workspace",
) -> dict[str, Any]:
    """Clone a built-in or user playbook into a writable copy."""
    if scope not in ("workspace", "global"):
        raise ValueError("scope must be workspace or global")
    from playbooks import PLAYBOOKS
    source: dict[str, Any] | None = None
    if source_id in PLAYBOOKS:
        source = PLAYBOOKS[source_id]
    else:
        existing = get_playbook(ws, source_id)
        if existing:
            source = existing
    if not source:
        raise ValueError(f"source playbook not found: {source_id}")

    target_id = _safe_id(new_id) if new_id else f"{_safe_id(source_id)}_copy"
    base = target_id
    n = 2
    while target_id in PLAYBOOKS or get_playbook(ws, target_id):
        target_id = f"{base}_{n}"
        n += 1
    payload = {
        "id": target_id,
        "label": f"{source.get('label', source_id)} (copy)",
        "tagline": source.get("tagline", ""),
        "expected_duration_s": source.get("expected_duration_s", 240),
        "accepts_source_types": list(source.get("accepts_source_types") or []),
        "artifact_type": source.get("artifact_type"),
        # Deep-copy steps so future edits don't mutate the original.
        "steps": json.loads(json.dumps(source.get("steps", []))),
        "scope": scope,
    }
    return create_playbook(ws, payload)
