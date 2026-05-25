"""Anonymous conversation threads — one JSON file per thread under data/conversations/.

Each thread is a sequence of turns. A turn is either:
- {"role": "user", "text": "...", "ts": <unix>}
- {"role": "assistant", "text": "...", "ts": <unix>,
   "entry_nodes": [...], "subgraph_node_ids": [...], "router_reasoning": "...",
   "router_used": bool, "fallback_used": bool}

Pinned findings live alongside turns in `pins: [{...}, ...]` for quick reference.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from workspaces import Workspace


def _safe_id(conv_id: str) -> str:
    safe = "".join(c for c in conv_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("Invalid conversation id")
    return safe


def _path(ws: Workspace, conv_id: str) -> Path:
    ws.ensure_dirs()
    return ws.conv_dir / f"{_safe_id(conv_id)}.json"


def _load(ws: Workspace, conv_id: str) -> dict[str, Any] | None:
    p = _path(ws, conv_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _save(ws: Workspace, conv: dict[str, Any]) -> None:
    _path(ws, conv["id"]).write_text(json.dumps(conv, indent=2))


def list_conversations(ws: Workspace) -> list[dict[str, Any]]:
    out = []
    if not ws.conv_dir.exists():
        return out
    for p in sorted(ws.conv_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            c = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "id": c["id"],
            "title": c.get("title", "Untitled"),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "turn_count": len(c.get("turns", [])),
            "pin_count": len(c.get("pins", [])),
        })
    return out


def create_conversation(
    ws: Workspace,
    title: str,
    intent: str | None = None,
    rubric_id: str | None = None,
    inference_strategy: str = "none",
    web_grounding: bool = False,
    auto_memory: bool = False,
    answer_model: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    conv = {
        "id": uuid.uuid4().hex[:12],
        "title": title.strip() or "Untitled",
        "intent": intent,
        "rubric_id": rubric_id,
        "inference_strategy": inference_strategy or "none",
        "web_grounding": bool(web_grounding),
        "auto_memory": bool(auto_memory),
        "answer_model": answer_model or None,
        "created_at": now,
        "updated_at": now,
        "turns": [],
        "pins": [],
    }
    _save(ws, conv)
    return conv


def update_settings(
    ws: Workspace,
    conv_id: str,
    *,
    intent: str | None = None,
    rubric_id: str | None = None,
    inference_strategy: str | None = None,
    web_grounding: bool | None = None,
    auto_memory: bool | None = None,
    answer_model: str | None = None,
) -> dict[str, Any] | None:
    conv = _load(ws, conv_id)
    if not conv:
        return None
    if intent is not None:
        conv["intent"] = intent or None
    if rubric_id is not None:
        conv["rubric_id"] = rubric_id or None
    if inference_strategy is not None:
        conv["inference_strategy"] = inference_strategy or "none"
    if web_grounding is not None:
        conv["web_grounding"] = bool(web_grounding)
    if auto_memory is not None:
        conv["auto_memory"] = bool(auto_memory)
    if answer_model is not None:
        conv["answer_model"] = answer_model or None
    conv["updated_at"] = time.time()
    _save(ws, conv)
    return conv


def get_conversation(ws: Workspace, conv_id: str) -> dict[str, Any] | None:
    return _load(ws, conv_id)


def delete_conversation(ws: Workspace, conv_id: str) -> bool:
    p = _path(ws, conv_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def rename_conversation(ws: Workspace, conv_id: str, title: str) -> dict[str, Any] | None:
    conv = _load(ws, conv_id)
    if not conv:
        return None
    conv["title"] = title.strip() or conv["title"]
    conv["updated_at"] = time.time()
    _save(ws, conv)
    return conv


def add_turn(ws: Workspace, conv_id: str, turn: dict[str, Any]) -> dict[str, Any] | None:
    """Append a user or assistant turn. Returns the updated conversation."""
    conv = _load(ws, conv_id)
    if not conv:
        return None
    turn = dict(turn)
    turn.setdefault("ts", time.time())
    conv["turns"].append(turn)
    conv["updated_at"] = turn["ts"]
    _save(ws, conv)
    return conv


def add_pin(ws: Workspace, conv_id: str, pin: dict[str, Any]) -> dict[str, Any] | None:
    """Pin a node, answer, or note to the conversation."""
    conv = _load(ws, conv_id)
    if not conv:
        return None
    pin = dict(pin)
    pin.setdefault("ts", time.time())
    pin.setdefault("id", uuid.uuid4().hex[:8])
    conv["pins"].append(pin)
    conv["updated_at"] = pin["ts"]
    _save(ws, conv)
    return conv


def remove_pin(ws: Workspace, conv_id: str, pin_id: str) -> dict[str, Any] | None:
    conv = _load(ws, conv_id)
    if not conv:
        return None
    conv["pins"] = [p for p in conv["pins"] if p.get("id") != pin_id]
    conv["updated_at"] = time.time()
    _save(ws, conv)
    return conv


def conversation_history_text(conv: dict[str, Any], max_turns: int = 8) -> str:
    """Render the last N turns as a plain-text transcript for prompt context."""
    turns = conv.get("turns", [])[-max_turns:]
    out = []
    for t in turns:
        role = t.get("role", "user").upper()
        out.append(f"{role}: {t.get('text', '').strip()}")
    return "\n\n".join(out)


# Cache for synthesize_scenarios. Keyed by (workspace_id, conv_id) → (cached_at,
# conv_updated_at, scenarios). TTL caps how long stale-but-still-fresh-enough
# scenarios are reused; the updated_at check invalidates immediately on any new
# turn so we never serve scenarios that don't reflect the latest exchange.
_SCENARIO_CACHE: dict[tuple[str, str], tuple[float, float, list[str]]] = {}
_SCENARIO_CACHE_TTL_S = 15 * 60


def synthesize_scenarios(ws: Workspace, conv_id: str) -> list[str]:
    """Distill the conversation into up to 3 candidate scenarios that ForeSight
    personas can debate. Uses the conversation's configured `answer_model` (so
    Sonnet conversations get Sonnet synthesis; Haiku conversations stay cheap).
    Cached for 15 minutes per (workspace, conversation), invalidated on any new
    turn (via conv.updated_at). Returns [] on any failure — callers fall back
    to the existing behaviour."""
    import json as _json
    import os as _os
    import re as _re
    # Local imports to avoid load-time cycles.
    from graphify_runner import _anthropic_client

    conv = _load(ws, conv_id)
    if not conv:
        return []
    turns = conv.get("turns", [])
    if not turns:
        return []

    cache_key = (ws.id, conv_id)
    conv_updated_at = float(conv.get("updated_at") or 0.0)
    cached = _SCENARIO_CACHE.get(cache_key)
    if cached is not None:
        cached_at, cached_updated_at, cached_scenarios = cached
        if (
            cached_scenarios
            and cached_updated_at == conv_updated_at
            and (time.time() - cached_at) < _SCENARIO_CACHE_TTL_S
        ):
            return cached_scenarios

    client = _anthropic_client()
    if not client:
        return []

    # Use the whole thread for synthesis — capped to last 20 turns so we don't
    # blow context on very long conversations.
    transcript = conversation_history_text(conv, max_turns=20)
    title = conv.get("title", "Untitled")

    prompt = (
        "You are reading a strategic conversation between a team and a research assistant. "
        "Your job is to distill it into THREE distinct forward-looking SCENARIOS that the team "
        "could simulate with debating personas (Bull, Bear, Customer, Competitor, Investor, etc.).\n\n"
        "Rules for each scenario:\n"
        "- A concrete forward-looking proposition (a bet, decision, or hypothesis), NOT a question.\n"
        "- 2-3 sentences. Specific about what, who, when (rough horizon ok).\n"
        "- Phrased so a Bull and a Bear could meaningfully disagree.\n"
        "- The three scenarios must be DISTINCT — different bets / framings, not rephrasings.\n"
        "- If the conversation explored only one direction, generate two close variants plus one "
        "deliberately-contrasting adjacent move.\n\n"
        f"Conversation title: {title}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY a JSON object: {\"scenarios\": [\"…\", \"…\", \"…\"]}. No markdown, no preamble."
    )

    # Honor the conversation's configured model so Sonnet conversations get
    # Sonnet synthesis. Fall back to the standard answer-model env default.
    model = conv.get("answer_model") or _os.environ.get(
        "GRAPHIFY_ANSWER_MODEL", "claude-haiku-4-5-20251001"
    )

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0.9,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        cleaned = _re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=_re.MULTILINE)
        parsed = _json.loads(cleaned)
        if not isinstance(parsed, dict):
            return []
        items = parsed.get("scenarios") or []
        scenarios = [str(s).strip() for s in items if str(s).strip()][:3]
        if scenarios:
            _SCENARIO_CACHE[cache_key] = (time.time(), conv_updated_at, scenarios)
        return scenarios
    except Exception:
        return []
