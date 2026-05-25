"""Persistent memory for InnoBrain Conversations — per workspace.

A memory item is a short, durable fact about the team, the corpus, or stable
preferences that should influence every conversation in this workspace.
Stored as one JSON file at data/workspaces/<id>/memory.json. Items are
prepended to the system prompt on every turn.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from workspaces import Workspace


def _load(ws: Workspace) -> list[dict[str, Any]]:
    p = ws.memory_file
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data.get("items", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        return []


def _save(ws: Workspace, items: list[dict[str, Any]]) -> None:
    ws.ensure_dirs()
    ws.memory_file.write_text(json.dumps({"items": items}, indent=2))


def list_items(ws: Workspace) -> list[dict[str, Any]]:
    return sorted(_load(ws), key=lambda x: x.get("updated_at", 0), reverse=True)


def add_item(ws: Workspace, text: str, source: str = "manual", tag: str | None = None) -> dict[str, Any]:
    items = _load(ws)
    now = time.time()
    item = {
        "id": uuid.uuid4().hex[:10],
        "text": text.strip(),
        "source": source,           # "manual" | "auto"
        "tag": tag,                 # optional category label
        "created_at": now,
        "updated_at": now,
    }
    items.append(item)
    _save(ws, items)
    return item


def update_item(ws: Workspace, mid: str, text: str | None = None, tag: str | None = None) -> dict[str, Any] | None:
    items = _load(ws)
    for it in items:
        if it["id"] == mid:
            if text is not None:
                it["text"] = text.strip()
            if tag is not None:
                it["tag"] = tag or None
            it["updated_at"] = time.time()
            _save(ws, items)
            return it
    return None


def delete_item(ws: Workspace, mid: str) -> bool:
    items = _load(ws)
    new_items = [it for it in items if it["id"] != mid]
    if len(new_items) == len(items):
        return False
    _save(ws, new_items)
    return True


def memory_block(ws: Workspace) -> str:
    """Render the workspace's memory as a prompt block for injection. Empty if no items."""
    items = list_items(ws)
    if not items:
        return ""
    lines = [f"- {it['text']}" for it in items]
    return "## Persistent memory (durable facts about the team and corpus)\n" + "\n".join(lines)


def auto_extract_candidates(
    ws: Workspace, client: Any, model: str, user_turn: str, assistant_turn: str
) -> list[str]:
    """Ask a small LLM call to identify durable facts worth remembering from a turn.

    Returns a list of memory-candidate strings (empty if nothing worth saving).
    """
    existing = "\n".join(f"- {it['text']}" for it in list_items(ws)[:30])
    prompt = (
        "You're managing a small research team's persistent memory. Below are the most recent "
        "user and assistant turns from a conversation. Identify any DURABLE facts worth saving — "
        "stable preferences, named projects, recurring constraints, persona details, decisions "
        "that should influence future conversations. Skip ephemeral details (today's question, "
        "single-use specifics). Return 0-3 candidates max.\n\n"
        f"Existing memory:\n{existing or '  (none yet)'}\n\n"
        f"USER:\n{user_turn[:1500]}\n\n"
        f"ASSISTANT:\n{assistant_turn[:1500]}\n\n"
        "Return ONLY this JSON, no preamble:\n"
        '{"candidates": ["fact 1", "fact 2"]}'
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return []
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    # Same lenient JSON-strip as elsewhere — keep this self-contained.
    import re
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
        return [c for c in data.get("candidates", []) if isinstance(c, str) and c.strip()]
    except json.JSONDecodeError:
        return []
