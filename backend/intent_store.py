"""User-defined intents — workspace-scoped or global.

Built-in intents stay in `rubrics.py` (hardcoded). User intents live as JSON
files alongside them. Lookup order at runtime: built-in > workspace > global.

Schema:
  {
    "id": "pm_competitive_audit",
    "group": "Product Manager",
    "label": "Audit the competitive landscape",
    "body": "Survey the top 5 competitors. For each: positioning, pricing, ...",
    "scope": "workspace" | "global",
    "created_at": float,
    "updated_at": float,
  }
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from workspaces import Workspace

GLOBAL_DIR = Path(__file__).parent / "data" / "global_intents"
GLOBAL_DIR.mkdir(parents=True, exist_ok=True)


def _safe_id(iid: str) -> str:
    safe = "".join(c for c in iid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid intent id")
    return safe


def _workspace_dir(ws: Workspace) -> Path:
    ws.ensure_dirs()
    d = ws.path / "intents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ws: Workspace | None, iid: str, scope: str) -> Path:
    if scope == "global":
        return GLOBAL_DIR / f"{_safe_id(iid)}.json"
    if ws is None:
        raise ValueError("workspace required for scope=workspace")
    return _workspace_dir(ws) / f"{_safe_id(iid)}.json"


def _save(ws: Workspace | None, intent: dict[str, Any]) -> None:
    _path(ws, intent["id"], intent["scope"]).write_text(json.dumps(intent, indent=2))


def list_intents(ws: Workspace) -> list[dict[str, Any]]:
    """All user-defined intents visible from this workspace: workspace + global."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Workspace takes precedence over global if both define the same id.
    ws_dir = ws.path / "intents"
    if ws_dir.exists():
        for p in sorted(ws_dir.glob("*.json")):
            try:
                intent = json.loads(p.read_text())
                intent["scope"] = "workspace"
                seen.add(intent["id"])
                out.append(intent)
            except json.JSONDecodeError:
                continue
    for p in sorted(GLOBAL_DIR.glob("*.json")):
        try:
            intent = json.loads(p.read_text())
            if intent["id"] in seen:
                continue
            intent["scope"] = "global"
            out.append(intent)
        except json.JSONDecodeError:
            continue
    return out


def get_intent(ws: Workspace, iid: str) -> dict[str, Any] | None:
    """Look up a user intent — workspace first, then global."""
    iid = _safe_id(iid)
    ws_path = _workspace_dir(ws) / f"{iid}.json"
    if ws_path.exists():
        intent = json.loads(ws_path.read_text())
        intent["scope"] = "workspace"
        return intent
    gpath = GLOBAL_DIR / f"{iid}.json"
    if gpath.exists():
        intent = json.loads(gpath.read_text())
        intent["scope"] = "global"
        return intent
    return None


def create_intent(
    ws: Workspace,
    *,
    iid: str,
    group: str,
    label: str,
    body: str,
    scope: str,
) -> dict[str, Any]:
    if scope not in ("workspace", "global"):
        raise ValueError("scope must be workspace or global")
    iid = _safe_id(iid)
    # Reject collision with anything visible from this workspace (built-in or user).
    from rubrics import INTENT_LABELS  # local import to avoid cycle at module load
    if iid in INTENT_LABELS:
        raise ValueError(f"id collides with built-in intent: {iid}")
    if get_intent(ws, iid):
        raise ValueError(f"intent already exists: {iid}")
    now = time.time()
    intent = {
        "id": iid,
        "group": group.strip() or "Custom",
        "label": label.strip() or iid,
        "body": body,
        "scope": scope,
        "created_at": now,
        "updated_at": now,
    }
    target_ws = ws if scope == "workspace" else None
    _save(target_ws, intent)
    return intent


def update_intent(
    ws: Workspace,
    iid: str,
    *,
    group: str | None = None,
    label: str | None = None,
    body: str | None = None,
    scope: str = "workspace",
) -> dict[str, Any] | None:
    """Patch a user intent — or materialize an override against a built-in id.

    First edit against a built-in iid creates an override seeded from the
    canonical built-in fields, then applies the patch. The override defaults
    to workspace scope so it stays local; pass scope="global" to override
    a built-in for every workspace."""
    intent = get_intent(ws, iid)
    if not intent:
        from rubrics import INTENT_LABELS, INTENT_GROUPS, intent_instruction
        if iid in INTENT_LABELS:
            group_label = ""
            for grp in INTENT_GROUPS:
                if iid in grp["intents"]:
                    group_label = grp["label"]
                    break
            if scope not in ("workspace", "global"):
                scope = "workspace"
            now = time.time()
            intent = {
                "id": iid,
                "group": group_label or "Custom",
                "label": INTENT_LABELS[iid],
                "body": intent_instruction(iid),
                "scope": scope,
                "created_at": now,
                "updated_at": now,
            }
        else:
            return None
    if group is not None:
        intent["group"] = group.strip() or intent["group"]
    if label is not None:
        intent["label"] = label.strip() or intent["label"]
    if body is not None:
        intent["body"] = body
    intent["updated_at"] = time.time()
    target_ws = ws if intent["scope"] == "workspace" else None
    _save(target_ws, intent)
    return intent


def delete_intent(ws: Workspace, iid: str) -> bool:
    """Delete a user intent — or for a built-in iid with an override, remove
    the override (which restores the canonical built-in)."""
    iid = _safe_id(iid)
    intent = get_intent(ws, iid)
    if not intent:
        return False
    target_ws = ws if intent["scope"] == "workspace" else None
    _path(target_ws, iid, intent["scope"]).unlink()
    return True


def is_builtin_override(ws: Workspace, iid: str) -> bool:
    """True if `iid` matches a built-in and a user override exists."""
    from rubrics import INTENT_LABELS
    if iid not in INTENT_LABELS:
        return False
    return get_intent(ws, iid) is not None


def restore_builtin_intent(ws: Workspace, iid: str) -> bool:
    """Drop a built-in override. Returns False if iid isn't a built-in or no
    override existed."""
    from rubrics import INTENT_LABELS
    if iid not in INTENT_LABELS:
        return False
    return delete_intent(ws, iid)


def clone_intent(
    ws: Workspace,
    source_id: str,
    *,
    new_id: str | None = None,
    scope: str = "workspace",
) -> dict[str, Any]:
    """Create a writable copy of a built-in or user intent.

    Source can be a built-in (looked up via rubrics.intent_instruction) or a
    user intent in this workspace. The clone always lands as a user intent.
    """
    if scope not in ("workspace", "global"):
        raise ValueError("scope must be workspace or global")
    from rubrics import INTENT_GROUPS, INTENT_LABELS, intent_instruction
    # Find source body + label + group.
    body = ""
    label = INTENT_LABELS.get(source_id, "")
    group = ""
    if source_id in INTENT_LABELS:
        body = intent_instruction(source_id)
        for grp in INTENT_GROUPS:
            if source_id in grp["intents"]:
                group = grp["label"]
                break
    else:
        existing = get_intent(ws, source_id)
        if not existing:
            raise ValueError(f"source intent not found: {source_id}")
        body = existing["body"]
        label = existing["label"]
        group = existing["group"]

    target_id = _safe_id(new_id) if new_id else f"{_safe_id(source_id)}_copy"
    # Avoid collision: bump with numeric suffix until unique.
    base = target_id
    n = 2
    while target_id in INTENT_LABELS or get_intent(ws, target_id):
        target_id = f"{base}_{n}"
        n += 1
    return create_intent(
        ws,
        iid=target_id,
        group=group or "Custom",
        label=f"{label} (copy)" if label else target_id,
        body=body,
        scope=scope,
    )
