"""Influence explainer — "why did the model produce this answer, and what could you change?"

A meta-prompt that takes a conversation turn or a playbook run's full context
and asks Claude to name the dominant influences (rubric clauses, memory items,
graph communities, web sources, intent framing) and propose concrete levers
the user can pull to get a different answer.

Two entry points:
- score_turn_influence(ws, conv, turn_idx) — single conversation turn,
  produces influences + actionable re-run levers (the UI wires them to
  settings PATCH + new turn POST).
- score_run_influence(ws, run) — playbook run, produces influences +
  cross-step convergence detection. Read-only: no levers (full re-runs
  are expensive and live in the Playbooks UI).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import intent_store
import memory as memory_store
import rubrics as rubric_store
from graphify_runner import _anthropic_client, load_graph
from workspaces import Workspace


_META_MODEL_DEFAULT = "claude-haiku-4-5-20251001"
# Limit how much we pack into the meta-prompt. Truncating defensively here
# keeps the meta-call cheap and prevents one giant turn from blowing the
# context window of the explainer.
_MAX_FIELD_CHARS = 4000
_MAX_PROMPT_CHARS = 24000


def _truncate(s: str, n: int = _MAX_FIELD_CHARS) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated, {len(s) - n} more chars]"


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Tolerant JSON extractor — accepts a fenced block or a bare object."""
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}


# ---------- Conversation-turn explainer -------------------------------------


def _community_counts_for_entry_nodes(
    ws: Workspace, entry_node_ids: list[str]
) -> list[dict[str, Any]]:
    """Return [{community_id, label, hits}] sorted by hits desc."""
    if not entry_node_ids:
        return []
    try:
        G = load_graph(ws)
    except Exception:
        return []
    if G is None:
        return []
    counts: dict[Any, dict[str, Any]] = {}
    for nid in entry_node_ids:
        if nid not in G.nodes:
            continue
        n = G.nodes[nid]
        cid = n.get("community")
        if cid is None:
            continue
        slot = counts.setdefault(
            cid,
            {"community_id": cid, "label": n.get("community_label") or f"Community {cid}", "hits": 0},
        )
        slot["hits"] += 1
        if not slot.get("label"):
            slot["label"] = n.get("community_label") or f"Community {cid}"
    return sorted(counts.values(), key=lambda x: x["hits"], reverse=True)


def _candidate_levers(
    ws: Workspace,
    conv: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate the menu of re-run levers offered to the user. Each lever
    is a {settings: ...} patch — the frontend applies it via the existing
    /api/conversations/{id}/settings PATCH, then POSTs a new turn.
    """
    levers: list[dict[str, Any]] = []

    # Web grounding toggle.
    web_on = bool(conv.get("web_grounding", False))
    levers.append({
        "id": "web_grounding_toggle",
        "label": f"Re-run with web grounding {'OFF' if web_on else 'ON'}",
        "expected_effect": (
            "Drops live-web context — useful if regulatory or news framing is biasing the synth."
            if web_on else
            "Adds live-web context — pulls in current dates, prices, and competitor status."
        ),
        "change": {"settings": {"web_grounding": not web_on}},
    })

    # Rubric swap — list rubrics in the workspace that aren't the current one.
    current_rubric = conv.get("rubric_id")
    try:
        all_rubrics = rubric_store.list_rubrics(ws)
    except Exception:
        all_rubrics = []
    rubric_alts = [r for r in all_rubrics if r.get("id") != current_rubric][:3]
    for r in rubric_alts:
        levers.append({
            "id": f"rubric_{r['id']}",
            "label": f"Re-run with rubric: {r.get('name', r['id'])}",
            "expected_effect": (
                f"Swaps the evaluation framing to '{r.get('name', r['id'])}' — "
                "different rules will reweight what counts as a valid answer."
            ),
            "change": {"settings": {"rubric_id": r["id"]}},
        })
    if current_rubric:
        levers.append({
            "id": "rubric_none",
            "label": "Re-run with no rubric",
            "expected_effect": "Removes all rubric constraints; freer brainstorm, less discipline.",
            "change": {"settings": {"rubric_id": None}},
        })

    # Intent swap — propose contrastive intents based on current one.
    current_intent = (conv.get("intent") or "explore").lower()
    contrast_map = {
        "explore": ["red_team", "outside_the_moat", "evaluate"],
        "product_idea": ["red_team", "outside_the_moat", "find_gaps"],
        "evaluate": ["red_team", "path_to_win", "outside_the_moat"],
        "synthesize": ["red_team", "outside_the_moat", "find_gaps"],
        "new_strategy": ["red_team", "outside_the_moat", "pre_mortem"],
        "find_gaps": ["outside_the_moat", "path_to_win", "external_scan"],
    }
    contrasts = contrast_map.get(current_intent, ["red_team", "outside_the_moat"])
    intent_labels = rubric_store.INTENT_LABELS
    for cid in contrasts:
        if cid == current_intent or cid not in intent_labels:
            continue
        levers.append({
            "id": f"intent_{cid}",
            "label": f"Re-run with intent: {intent_labels[cid]}",
            "expected_effect": f"Reframes the turn through the '{cid}' lens.",
            "change": {"settings": {"intent": cid}},
        })

    # Model swap — offer the model NOT currently selected.
    current_model = conv.get("answer_model") or os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-haiku-4-5-20251001")
    model_order = [
        ("claude-haiku-4-5-20251001", "Haiku 4.5", "fast & cheap"),
        ("claude-sonnet-4-6", "Sonnet 4.6", "balanced"),
        ("claude-opus-4-7", "Opus 4.7", "highest quality"),
    ]
    next_model = None
    for mid, mname, mhint in model_order:
        if mid != current_model:
            next_model = (mid, mname, mhint)
            break
    if next_model:
        mid, mname, mhint = next_model
        levers.append({
            "id": f"model_{mid}",
            "label": f"Re-run with model: {mname}",
            "expected_effect": f"Different model ({mhint}). Often shifts framing as much as content.",
            "change": {"settings": {"answer_model": mid}},
        })

    return levers


def _conv_setting_summary(conv: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": conv.get("intent"),
        "rubric_id": conv.get("rubric_id"),
        "inference_strategy": conv.get("inference_strategy", "none"),
        "web_grounding": bool(conv.get("web_grounding", False)),
        "answer_model": conv.get("answer_model") or os.environ.get("GRAPHIFY_ANSWER_MODEL"),
        "auto_memory": bool(conv.get("auto_memory", False)),
    }


def score_turn_influence(
    ws: Workspace, conv: dict[str, Any], turn_idx: int
) -> dict[str, Any]:
    """Score the influences on a specific assistant turn.

    Builds a meta-prompt with the question, the answer, and the *named*
    things that shaped it (rubric body, memory items, communities touched
    by the router, web sources, intent text). Asks Claude to attribute
    weight to each and explain WHY it pulled the answer this way.

    Returns:
      {
        question, answer_preview, settings, influences: [...],
        levers: [...], note
      }
    """
    turns = conv.get("turns", [])
    if turn_idx < 0 or turn_idx >= len(turns):
        raise ValueError(f"turn_idx {turn_idx} out of range")
    turn = turns[turn_idx]
    if turn.get("role") != "assistant":
        raise ValueError("can only explain assistant turns")

    # Find the user question paired with this assistant turn.
    question = ""
    for prior in reversed(turns[:turn_idx]):
        if prior.get("role") == "user":
            question = prior.get("text", "")
            break

    settings = _conv_setting_summary(conv)

    # --- Context blocks for the meta-prompt -------------------------------
    intent_text = rubric_store.intent_instruction(conv.get("intent"), ws)
    rubric_body = ""
    rubric_name = None
    if conv.get("rubric_id"):
        r = rubric_store.get_rubric(ws, conv["rubric_id"])
        if r:
            rubric_body = r.get("body", "")
            rubric_name = r.get("name", conv["rubric_id"])

    mem_items = memory_store.list_items(ws)
    mem_for_prompt = [
        {"id": m.get("id"), "source": m.get("source", "?"), "text": _truncate(m.get("text", ""), 400)}
        for m in mem_items
    ]

    entry_node_ids = turn.get("entry_node_ids", []) or []
    entry_node_labels = turn.get("entry_node_labels", []) or []
    communities = _community_counts_for_entry_nodes(ws, entry_node_ids)
    web_sources = turn.get("web_sources", []) or []

    # --- Build the meta-prompt --------------------------------------------
    client = _anthropic_client()
    if not client:
        return _heuristic_only(turn, conv, communities, mem_for_prompt, web_sources, settings, rubric_name)

    prompt_payload = {
        "question": _truncate(question, 1500),
        "answer": _truncate(turn.get("text", ""), 3000),
        "settings": settings,
        "intent_instruction_preview": _truncate(intent_text, 500),
        "rubric": {
            "id": conv.get("rubric_id"),
            "name": rubric_name,
            "body_preview": _truncate(rubric_body, 1500),
        } if rubric_body else None,
        "memory_items": mem_for_prompt,
        "router_entry_nodes": [
            {"id": nid, "label": lbl}
            for nid, lbl in zip(entry_node_ids[:10], entry_node_labels[:10])
        ],
        "router_reasoning": _truncate(turn.get("router_reasoning", ""), 600),
        "community_hits": communities,
        "web_sources": [
            {"title": w.get("title", ""), "url": w.get("url", "")} for w in web_sources[:8]
        ],
        "gaps": turn.get("gaps", []),
        "inference_strategy": turn.get("inference_strategy"),
    }
    payload_json = json.dumps(prompt_payload, indent=2)
    if len(payload_json) > _MAX_PROMPT_CHARS:
        payload_json = payload_json[:_MAX_PROMPT_CHARS] + "\n…[payload truncated]"

    system = (
        "You are an explainability auditor for an LLM-grounded knowledge-graph "
        "system. You will be given everything that shaped a single assistant "
        "answer — the user question, the answer, the rubric body, the memory "
        "items injected, the graph nodes the router landed on, the web sources, "
        "and the intent framing. Your job: name the 3-6 INFLUENCES that most "
        "shaped the answer, and rank them.\n\n"
        "For each influence: kind (rubric / memory / community / web / intent / "
        "model / strategy), id (the specific identifier from the payload), label "
        "(short human-readable), weight (high / medium / low), and ONE sentence "
        "of concrete evidence — quote or paraphrase the specific clause / fact / "
        "label that bent the answer.\n\n"
        "Be ruthlessly specific. 'The rubric biased the answer' is useless; "
        "'Rubric clause #2 (Sherlocking test) pushed the synth toward existing "
        "Marketplace bundling' is useful.\n\n"
        "Output STRICT JSON only, no prose. Schema:\n"
        "{\n"
        '  "summary": "1-2 sentence plain-language summary of what pulled this answer",\n'
        '  "influences": [\n'
        '    {"kind": "rubric|memory|community|web|intent|model|strategy",\n'
        '     "id": "...", "label": "...", "weight": "high|medium|low",\n'
        '     "evidence": "specific 1-sentence reason"}\n'
        "  ]\n"
        "}"
    )

    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_INFLUENCE_MODEL", _META_MODEL_DEFAULT),
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": payload_json}],
        )
    except Exception as exc:  # noqa: BLE001
        return _heuristic_only(
            turn, conv, communities, mem_for_prompt, web_sources, settings, rubric_name,
            error=str(exc),
        )

    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text")
    parsed = _parse_json_loose(text)
    influences = parsed.get("influences") or []
    summary = parsed.get("summary") or ""

    # Always offer the structured lever list independent of the LLM call —
    # the LLM names *what* shaped the answer; we name *what to change*.
    levers = _candidate_levers(ws, conv)

    return {
        "turn_idx": turn_idx,
        "question": question,
        "answer_preview": _truncate(turn.get("text", ""), 600),
        "settings": settings,
        "summary": summary,
        "influences": influences,
        "levers": levers,
        "raw_signals": {
            "community_hits": communities,
            "memory_count": len(mem_items),
            "web_source_count": len(web_sources),
            "entry_node_count": len(entry_node_ids),
        },
    }


def _heuristic_only(
    turn: dict[str, Any],
    conv: dict[str, Any],
    communities: list[dict[str, Any]],
    mem_items: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
    settings: dict[str, Any],
    rubric_name: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Fallback when no LLM is available — return only the deterministic signals."""
    influences: list[dict[str, Any]] = []
    if rubric_name:
        influences.append({
            "kind": "rubric", "id": conv.get("rubric_id"), "label": f"Rubric: {rubric_name}",
            "weight": "high", "evidence": "Rubric body was appended to the system prompt for this turn.",
        })
    if mem_items:
        influences.append({
            "kind": "memory", "id": "memory_block",
            "label": f"{len(mem_items)} memory item(s) injected",
            "weight": "high" if len(mem_items) >= 3 else "medium",
            "evidence": "Each memory item is prepended to every turn's system prompt.",
        })
    for c in communities[:3]:
        influences.append({
            "kind": "community", "id": str(c["community_id"]),
            "label": f"Community: {c['label']}",
            "weight": "high" if c["hits"] >= 3 else "medium",
            "evidence": f"{c['hits']} router entry nodes fell in this community.",
        })
    if web_sources:
        influences.append({
            "kind": "web", "id": "web_sources",
            "label": f"{len(web_sources)} web source(s)",
            "weight": "medium",
            "evidence": "Live-web claims were grounded into the answer.",
        })
    return {
        "turn_idx": -1,
        "question": "",
        "answer_preview": "",
        "settings": settings,
        "summary": "Heuristic-only explainer (LLM unavailable)." + (f" {error}" if error else ""),
        "influences": influences,
        "levers": [],
        "raw_signals": {
            "community_hits": communities,
            "memory_count": len(mem_items),
            "web_source_count": len(web_sources),
        },
    }


# ---------- Playbook-run explainer ------------------------------------------


def _step_excerpts(run: dict[str, Any], chars_per_step: int = 800) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in run.get("steps", []):
        if s.get("status") != "complete":
            continue
        out.append({
            "id": s.get("id"),
            "label": s.get("label"),
            "type": s.get("type"),
            "excerpt": _truncate(s.get("output", ""), chars_per_step),
            "web_source_count": len(s.get("web_sources", []) or []),
        })
    return out


def score_run_influence(ws: Workspace, run: dict[str, Any]) -> dict[str, Any]:
    """Score the influences on a full playbook run.

    The interesting question for a multi-step run is: did the steps converge
    on a single theme? If yes, name it and identify which upstream factor
    (rubric / memory / corpus density) most likely drove that convergence.
    Read-only; no levers (re-runs go through the Playbooks tab).
    """
    inputs = run.get("user_inputs", {}) or {}

    rubric_id = inputs.get("rubric_id")
    rubric_body = ""
    rubric_name = None
    if rubric_id:
        r = rubric_store.get_rubric(ws, rubric_id)
        if r:
            rubric_body = r.get("body", "")
            rubric_name = r.get("name", rubric_id)

    mem_items = memory_store.list_items(ws)
    mem_for_prompt = [
        {"id": m.get("id"), "source": m.get("source", "?"), "text": _truncate(m.get("text", ""), 400)}
        for m in mem_items
    ]

    step_excerpts = _step_excerpts(run)
    web_total = sum(s.get("web_source_count", 0) for s in step_excerpts)

    client = _anthropic_client()
    if not client:
        return {
            "run_id": run.get("id"),
            "playbook_id": run.get("playbook_id"),
            "summary": "Heuristic-only explainer (LLM unavailable).",
            "influences": [
                {"kind": "rubric", "id": rubric_id, "label": f"Rubric: {rubric_name or '(none)'}",
                 "weight": "high" if rubric_body else "low",
                 "evidence": "Rubric was applied to every step."},
                {"kind": "memory", "id": "memory_block",
                 "label": f"{len(mem_items)} memory item(s)",
                 "weight": "high" if len(mem_items) >= 3 else "medium",
                 "evidence": "Each memory item is injected into every step's system prompt."},
            ] if (rubric_body or mem_items) else [],
            "levers": [],
            "note": "Playbook re-runs are expensive — change settings on a fresh run in the Playbooks tab.",
        }

    prompt_payload = {
        "scenario": _truncate(inputs.get("scenario", ""), 1200),
        "playbook_id": run.get("playbook_id"),
        "playbook_label": run.get("playbook_label"),
        "settings": {
            "horizon": inputs.get("horizon"),
            "rubric_id": rubric_id,
            "rubric_name": rubric_name,
            "web_grounding": inputs.get("web_grounding"),
            "answer_model": inputs.get("answer_model"),
            "synth_inference_strategy": inputs.get("synth_inference_strategy"),
        },
        "rubric_body_preview": _truncate(rubric_body, 1500) if rubric_body else None,
        "memory_items": mem_for_prompt,
        "steps": step_excerpts,
        "total_web_sources": web_total,
    }
    payload_json = json.dumps(prompt_payload, indent=2)
    if len(payload_json) > _MAX_PROMPT_CHARS:
        payload_json = payload_json[:_MAX_PROMPT_CHARS] + "\n…[payload truncated]"

    system = (
        "You are an explainability auditor for a multi-step LLM playbook. "
        "You will be given the user's scenario, the rubric applied to every "
        "step, the memory items injected into every step, and a short excerpt "
        "from each step's output. Your job:\n\n"
        "1. Name the 3-6 INFLUENCES that most shaped the run's final direction.\n"
        "2. Detect CROSS-STEP CONVERGENCE: if N of the steps' outputs landed "
        "   on the same theme / product line / framing — especially if the "
        "   scenario did not mandate it — surface that explicitly as a "
        "   'step_convergence' influence with HIGH weight. Name the theme.\n\n"
        "For each influence: kind (rubric / memory / step_convergence / "
        "scenario_anchor / web / model / strategy), id (specific identifier), "
        "label (short), weight (high / medium / low), and ONE sentence of "
        "concrete evidence — name specific steps / specific clauses.\n\n"
        "Output STRICT JSON only:\n"
        "{\n"
        '  "summary": "1-2 sentence plain-language take",\n'
        '  "convergence_theme": "string or null",\n'
        '  "influences": [{"kind","id","label","weight","evidence"}]\n'
        "}"
    )

    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_INFLUENCE_MODEL", _META_MODEL_DEFAULT),
            max_tokens=1600,
            system=system,
            messages=[{"role": "user", "content": payload_json}],
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "run_id": run.get("id"),
            "playbook_id": run.get("playbook_id"),
            "summary": f"Influence scoring failed: {exc}",
            "influences": [],
            "levers": [],
            "note": "Try again, or check the backend logs.",
        }

    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text")
    parsed = _parse_json_loose(text)

    return {
        "run_id": run.get("id"),
        "playbook_id": run.get("playbook_id"),
        "summary": parsed.get("summary") or "",
        "convergence_theme": parsed.get("convergence_theme"),
        "influences": parsed.get("influences") or [],
        "levers": [],
        "raw_signals": {
            "rubric_id": rubric_id,
            "rubric_name": rubric_name,
            "memory_count": len(mem_items),
            "step_count": len(step_excerpts),
            "total_web_sources": web_total,
        },
        "note": "Playbook re-runs are expensive — to explore alternatives, change settings on a fresh run in the Playbooks tab.",
    }
