"""ForeSight — the heavyweight multi-agent scenario simulator.

This is separate from the lightweight Conversations simulator in simulate.py.
ForeSight does:
  - configurable persona count (1-12 personas per run)
  - custom persona definitions (CRUD against data/foresight_personas/)
  - multi-round debate (Round 2+ personas see all Round 1 outputs)
  - persisted sessions (data/foresight/{id}.json) you can revisit
  - final synthesis: convergent / divergent / most-likely / watch indicators

The simple 4-persona /api/conversations/{id}/simulate stays in simulate.py and
is untouched. ForeSight is reached via the new /foresight tab in the UI.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from workspaces import Workspace

# Personas are a reusable, GLOBAL library (like rubrics). Sessions are per-workspace.
PERSONA_DIR = Path(__file__).parent / "data" / "foresight_personas"
PERSONA_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS: dict[str, str] = {
    "3mo": "3 months from now",
    "6mo": "6 months from now",
    "1y": "1 year from now",
    "3y": "3 years from now",
    "5y": "5 years from now",
}

# ---------- Preset personas (immutable; identified by source="preset") ------

PRESET_PERSONAS: dict[str, dict[str, str]] = {
    "bull": {
        "label": "Bull",
        "tagline": "what goes right",
        "color": "#34d399",
        "system": (
            "You are the BULL voice. Argue the most plausible upside path. Identify what has to "
            "go right, who benefits, leading signal you'd watch. Concrete, not cheerleader-ish."
        ),
    },
    "bear": {
        "label": "Bear",
        "tagline": "what goes wrong",
        "color": "#fb7185",
        "system": (
            "You are the BEAR voice. Argue the strongest downside case. Identify specific failure "
            "modes, what makes them likely, and the earliest indicator. Be specific, not generic doom."
        ),
    },
    "customer": {
        "label": "Customer",
        "tagline": "voice of the buyer",
        "color": "#818cf8",
        "system": (
            "You are the CUSTOMER voice — the buyer or end-user paying for the outcome. What "
            "changes for you? What's your alternative? When would you say 'no thanks'?"
        ),
    },
    "competitor": {
        "label": "Competitor",
        "tagline": "what the field does in response",
        "color": "#fbbf24",
        "system": (
            "You are the COMPETITOR voice. You run a rival company or the incumbent. How do you "
            "respond? What's your strongest counter? What's the single move that neutralises this?"
        ),
    },
    "enterprise_first": {
        "label": "Enterprise-first",
        "tagline": "big logos, governance, procurement",
        "color": "#22d3ee",
        "system": (
            "You are the ENTERPRISE-FIRST voice. Optimise for landing and retaining $100K+ ACV "
            "logos. Speak about procurement cycles, security/compliance hurdles, executive "
            "sponsorship, multi-year contracts, governance, and the risk that a feature that wins "
            "SMB hearts is irrelevant to a CISO."
        ),
    },
    "retention_first": {
        "label": "Retention-first",
        "tagline": "churn, expansion, customer success",
        "color": "#a78bfa",
        "system": (
            "You are the RETENTION-FIRST voice. Net Revenue Retention is your KPI. Focus on what "
            "this scenario does to churn, upsell motion, time-to-value, support load, and the "
            "boring-but-fatal risks (renewal friction, feature deprecation, integration breakage)."
        ),
    },
    "growth_first": {
        "label": "Growth-first",
        "tagline": "new logos, top of funnel, virality",
        "color": "#f472b6",
        "system": (
            "You are the GROWTH-FIRST voice. New-logo velocity is your KPI. Care about top-of-"
            "funnel acquisition cost, self-serve conversion, paid acquisition payback, virality "
            "loops, and whether this scenario unlocks distribution leverage."
        ),
    },
    "delivery_first": {
        "label": "Delivery-first",
        "tagline": "ship dates, scope discipline, ops",
        "color": "#fb923c",
        "system": (
            "You are the DELIVERY-FIRST voice — the engineering/ops leader who has to actually "
            "ship this. Care about scope creep, hidden dependencies, on-call load, deployment "
            "complexity, hiring lead times. Skeptical of plans that ignore execution gravity."
        ),
    },
    "platform_first": {
        "label": "Platform-first",
        "tagline": "infrastructure, extensibility, devex",
        "color": "#60a5fa",
        "system": (
            "You are the PLATFORM-FIRST voice. Care about durability of investment, API surface "
            "stability, third-party developer ecosystem, technical debt, and whether this scenario "
            "compounds platform value or creates one-off vertical detours."
        ),
    },
    "regulator": {
        "label": "Regulator",
        "tagline": "compliance, audit, public risk",
        "color": "#facc15",
        "system": (
            "You are the REGULATOR voice — a public-sector enforcement officer or industry "
            "auditor. What in this scenario crosses a line? What new precedent worries you? What "
            "data, disclosure, or controls would you require before approving?"
        ),
    },
    "investor": {
        "label": "Investor",
        "tagline": "capital efficiency, terminal value",
        "color": "#94a3b8",
        "system": (
            "You are the INVESTOR voice — a board member or LP. Care about capital efficiency, "
            "magnitude of upside, exit multiple, time to value, opportunity cost, and whether the "
            "story you'd tell at the next board meeting still makes sense in this scenario."
        ),
    },
}


def _preset_disk_name(key: str) -> str:
    """Filename used for a preset override. The `preset__` prefix can never
    collide with a uuid-hex custom-persona filename (uuids contain no `_`)."""
    safe = "".join(c for c in key if c.isalnum() or c in "-_")
    return f"preset__{safe}"


def _preset_override_path(key: str) -> Path:
    return PERSONA_DIR / f"{_preset_disk_name(key)}.json"


def _preset_view(key: str, override: dict[str, Any] | None) -> dict[str, Any] | None:
    """Materialize a preset (with optional override fields applied) into the
    response shape. Source flips to `customized` when an override exists."""
    base = PRESET_PERSONAS.get(key)
    if not base:
        return None
    view = {
        "id": f"preset:{key}",
        "key": key,
        "source": "preset",
        "label": base["label"],
        "tagline": base["tagline"],
        "color": base["color"],
        "system": base["system"],
    }
    if override:
        view.update({
            "label": override.get("label", view["label"]),
            "tagline": override.get("tagline", view["tagline"]),
            "color": override.get("color", view["color"]),
            "system": override.get("system", view["system"]),
            "source": "customized",
            "updated_at": override.get("updated_at"),
        })
    return view


def list_personas() -> list[dict[str, Any]]:
    """Return preset + custom personas. Preset overrides (stored on disk under
    `preset__{key}.json`) are merged on top of the preset registry."""
    out: list[dict[str, Any]] = []
    for key in PRESET_PERSONAS:
        override = None
        p = _preset_override_path(key)
        if p.exists():
            try:
                override = json.loads(p.read_text())
            except json.JSONDecodeError:
                override = None
        view = _preset_view(key, override)
        if view:
            out.append(view)
    for f in sorted(PERSONA_DIR.glob("*.json")):
        if f.stem.startswith("preset__"):
            continue  # override files surface as part of the preset row above
        try:
            data = json.loads(f.read_text())
            data["source"] = "custom"
            out.append(data)
        except json.JSONDecodeError:
            continue
    return out


def get_persona(pid: str) -> dict[str, Any] | None:
    if pid.startswith("preset:"):
        key = pid.split(":", 1)[1]
        if key not in PRESET_PERSONAS:
            return None
        override = None
        p = _preset_override_path(key)
        if p.exists():
            try:
                override = json.loads(p.read_text())
            except json.JSONDecodeError:
                override = None
        return _preset_view(key, override)
    safe = "".join(c for c in pid if c.isalnum() or c in "-_")
    if safe.startswith("preset__"):
        return None  # internal override files are not addressable by raw filename
    p = PERSONA_DIR / f"{safe}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    data["source"] = "custom"
    return data


def create_custom_persona(label: str, tagline: str, system: str, color: str | None = None) -> dict[str, Any]:
    now = time.time()
    pid = uuid.uuid4().hex[:10]
    data = {
        "id": pid,
        "source": "custom",
        "label": label.strip() or "Untitled persona",
        "tagline": (tagline or "").strip(),
        "system": system.strip(),
        "color": color or "#94a3b8",
        "created_at": now,
        "updated_at": now,
    }
    (PERSONA_DIR / f"{pid}.json").write_text(json.dumps(data, indent=2))
    return data


def update_custom_persona(pid: str, *, label: str | None = None, tagline: str | None = None,
                          system: str | None = None, color: str | None = None) -> dict[str, Any] | None:
    """Patch a custom persona, OR save an override against a preset id.

    First edit against a `preset:xxx` id materializes an override file seeded
    from the preset's current fields, then applies the patch."""
    if pid.startswith("preset:"):
        key = pid.split(":", 1)[1]
        base = PRESET_PERSONAS.get(key)
        if not base:
            return None
        p = _preset_override_path(key)
        if p.exists():
            data = json.loads(p.read_text())
        else:
            now = time.time()
            data = {
                "id": pid,
                "label": base["label"],
                "tagline": base["tagline"],
                "system": base["system"],
                "color": base["color"],
                "created_at": now,
                "updated_at": now,
            }
        if label is not None:
            data["label"] = label.strip() or data["label"]
        if tagline is not None:
            data["tagline"] = tagline.strip()
        if system is not None:
            data["system"] = system.strip() or data["system"]
        if color is not None:
            data["color"] = color or data["color"]
        data["updated_at"] = time.time()
        p.write_text(json.dumps(data, indent=2))
        return _preset_view(key, data)
    data = get_persona(pid)
    if not data:
        return None
    if label is not None:
        data["label"] = label.strip() or data["label"]
    if tagline is not None:
        data["tagline"] = tagline.strip()
    if system is not None:
        data["system"] = system.strip() or data["system"]
    if color is not None:
        data["color"] = color or data["color"]
    data["updated_at"] = time.time()
    (PERSONA_DIR / f"{data['id']}.json").write_text(json.dumps(data, indent=2))
    return data


def delete_custom_persona(pid: str) -> bool:
    """Delete a custom persona. For preset ids: only removes the override
    (restores the preset). Returns False if there's nothing to delete."""
    if pid.startswith("preset:"):
        key = pid.split(":", 1)[1]
        p = _preset_override_path(key)
        if not p.exists():
            return False
        p.unlink()
        return True
    safe = "".join(c for c in pid if c.isalnum() or c in "-_")
    if safe.startswith("preset__"):
        return False
    p = PERSONA_DIR / f"{safe}.json"
    if not p.exists():
        return False
    p.unlink()
    return True


def restore_preset_persona(pid: str) -> dict[str, Any] | None:
    """Drop a preset override and return the canonical preset."""
    if not pid.startswith("preset:"):
        return None
    key = pid.split(":", 1)[1]
    if key not in PRESET_PERSONAS:
        return None
    p = _preset_override_path(key)
    if p.exists():
        p.unlink()
    return _preset_view(key, None)


# ---------- Sessions (per workspace) ----------------------------------------

def _session_path(ws: Workspace, sid: str) -> Path:
    safe = "".join(c for c in sid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid session id")
    ws.ensure_dirs()
    return ws.foresight_dir / f"{safe}.json"


def list_sessions(ws: Workspace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not ws.foresight_dir.exists():
        return out
    for p in sorted(ws.foresight_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            s = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "id": s["id"],
            "title": s.get("title", "Untitled"),
            "scenario": s.get("scenario", ""),
            "horizon": s.get("horizon", "1y"),
            "horizon_label": HORIZONS.get(s.get("horizon", "1y"), s.get("horizon", "1y")),
            "persona_count": len(s.get("personas", [])),
            "rounds": s.get("rounds", 1),
            "status": s.get("status", "draft"),
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
        })
    return out


def create_session(
    ws: Workspace,
    title: str,
    scenario: str,
    horizon: str = "1y",
    persona_ids: list[str] | None = None,
    rounds: int = 2,
    world_context: str = "",
    rubric_id: str | None = None,
    use_graph: bool = True,
    answer_model: str | None = None,
    source_conversation_id: str | None = None,
    source_conversation_title: str | None = None,
    use_memory: bool = True,
    web_grounding: bool = False,
    synth_inference_strategy: str = "none",
) -> dict[str, Any]:
    now = time.time()
    session = {
        "id": uuid.uuid4().hex[:12],
        "title": (title or "Untitled foresight").strip(),
        "scenario": scenario.strip(),
        "horizon": horizon if horizon in HORIZONS else "1y",
        "rounds": max(1, min(int(rounds), 3)),
        "world_context": world_context.strip(),
        "personas": [pid for pid in (persona_ids or []) if get_persona(pid)],
        "rubric_id": rubric_id,
        "use_graph": bool(use_graph),
        "answer_model": answer_model,
        "source_conversation_id": source_conversation_id,
        # Snapshot of the linked conversation's title at creation. Survives even
        # if the source conversation is later renamed or deleted.
        "source_conversation_title": source_conversation_title,
        "use_memory": bool(use_memory),
        "web_grounding": bool(web_grounding),
        "synth_inference_strategy": (
            synth_inference_strategy if synth_inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"
        ),
        "status": "draft",
        "output": None,
        "created_at": now,
        "updated_at": now,
    }
    _session_path(ws, session["id"]).write_text(json.dumps(session, indent=2))
    return session


def get_session(ws: Workspace, sid: str) -> dict[str, Any] | None:
    p = _session_path(ws, sid)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def update_session(ws: Workspace, sid: str, **fields: Any) -> dict[str, Any] | None:
    s = get_session(ws, sid)
    if not s:
        return None
    allowed = {
        "title", "scenario", "horizon", "rounds", "world_context",
        "personas", "rubric_id", "use_graph", "answer_model",
        "source_conversation_id", "source_conversation_title", "use_memory",
        "web_grounding", "synth_inference_strategy",
    }
    for k, v in fields.items():
        if k in allowed and v is not None:
            s[k] = v
    s["updated_at"] = time.time()
    _session_path(ws, sid).write_text(json.dumps(s, indent=2))
    return s


def delete_session(ws: Workspace, sid: str) -> bool:
    p = _session_path(ws, sid)
    if not p.exists():
        return False
    p.unlink()
    return True


def _save_session(ws: Workspace, s: dict[str, Any]) -> None:
    s["updated_at"] = time.time()
    _session_path(ws, s["id"]).write_text(json.dumps(s, indent=2))


# ---------- Orchestration ---------------------------------------------------

# Shared style clause folded into every persona + synthesis system prompt.
# Hard brevity rules — ForeSight output was a wall of text before this.
_HOUSE_STYLE = (
    "House style — CLEAR, CONCISE, COHERENT, COMPLETE:\n"
    "- No preamble, no recap, no 'In summary'. Start with the substance.\n"
    "- Short sentences. No hedges ('arguably', 'it seems'). No filler adjectives.\n"
    "- Concrete > abstract. Numbers, names, dates over generalities.\n"
    "- If a bullet, one idea per bullet, ≤ 18 words.\n"
    "- Respect the word/sentence cap the user gives. Don't pad to fill space.\n"
    "PRESERVE — never drop load-bearing detail:\n"
    "- Every concrete number, dollar amount, percentage, ratio, date, deadline.\n"
    "- Every named entity: company, product, person, regulation, file.\n"
    "- Every source_file citation and 'web: domain.com' attribution.\n"
    "- Verdicts (kill gates, go/no-go, recommendations) stay byte-identical when cited.\n"
    "If you must choose between brevity and a load-bearing fact, KEEP THE FACT and trim prose instead."
)


def _persona_first_round(
    client: Any, model: str, persona: dict[str, Any],
    scenario: str, horizon_phrase: str, world_context: str,
    graph_context: str, rubric_body: str,
    memory_block: str = "",
    conversation_history: str = "",
    web_grounding: bool = False,
) -> dict[str, Any]:
    system_parts = [_HOUSE_STYLE]
    if memory_block:
        system_parts.append(memory_block)
    system_parts.append(persona["system"])
    if rubric_body:
        system_parts.append("Apply these framing rules:\n" + rubric_body)
    system = "\n\n".join(system_parts)

    user_blocks = [
        f"Scenario: {scenario}",
        f"Time horizon: {horizon_phrase}",
    ]
    if conversation_history:
        user_blocks.append(
            "Prior conversation context (do not repeat — build on it):\n" + conversation_history
        )
    if world_context:
        user_blocks.append(f"World context (assume true):\n{world_context}")
    if graph_context:
        user_blocks.append(f"Knowledge graph context:\n{graph_context}")
    user_blocks.append(
        f"Speak only as the {persona['label']} voice. **3 short sentences max** "
        "(no more than ~70 words total). End with one line starting `WATCH: ` — "
        "the earliest indicator your scenario is playing out."
    )

    return _call_persona(client, model, persona, "\n\n".join(user_blocks), system, web_grounding=web_grounding)


def _persona_reaction_round(
    client: Any, model: str, persona: dict[str, Any],
    scenario: str, horizon_phrase: str, world_context: str,
    own_prior: str, others_prior: list[dict[str, str]],
    rubric_body: str, round_num: int,
    memory_block: str = "",
    web_grounding: bool = False,
) -> dict[str, Any]:
    system_parts = [_HOUSE_STYLE]
    if memory_block:
        system_parts.append(memory_block)
    system_parts.append(persona["system"])
    if rubric_body:
        system_parts.append("Apply these framing rules:\n" + rubric_body)
    system = "\n\n".join(system_parts)

    others_block = "\n\n".join(
        f"**{o['label']}:**\n{o['text']}" for o in others_prior
    )

    user = (
        f"Scenario: {scenario}\nHorizon: {horizon_phrase}\n"
        + (f"World context: {world_context}\n" if world_context else "")
        + f"\nYour prior position:\n{own_prior}\n\n"
        + f"Round {round_num - 1} from the others:\n\n{others_block}\n\n"
        + "React. Sharpen where you're right; update where evidence shifts you. "
          "Name the personas you're responding to. No politeness, no splitting the "
          "difference. **3 short sentences max** (~70 words). End with an updated "
          "`WATCH: ` line only if your indicator actually changed."
    )

    return _call_persona(client, model, persona, user, system, web_grounding=web_grounding)


def _call_persona(
    client: Any, model: str, persona: dict[str, Any], user: str, system: str,
    *, web_grounding: bool = False,
) -> dict[str, Any]:
    if web_grounding:
        system = system + (
            "\n\nYou have a web_search tool. Use it ONLY to verify time-sensitive "
            "facts (dates, deadlines, company status, regulations, current product "
            "availability) before stating them. Cite as 'web: domain.com'."
        )
    kwargs: dict[str, Any] = {
        "model": model,
        # Tight cap is the second line of defense after the "3 sentences"
        # instruction — keeps personas from drifting into walls of text.
        "max_tokens": 500 if web_grounding else 350,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_grounding:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "3")),
        }]
    try:
        msg = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        from graphify_runner import humanize_anthropic_error as _human
        return {
            "persona_id": persona["id"], "label": persona["label"],
            "color": persona.get("color"),
            "text": f"(failed: {_human(exc)})", "tokens": {"input": 0, "output": 0},
            "web_sources": [],
        }
    text_parts: list[str] = []
    web_sources: list[dict[str, str]] = []
    for b in msg.content:
        t = getattr(b, "type", None)
        if t == "text":
            text_parts.append(b.text)
        elif t == "web_search_tool_result":
            results = getattr(b, "content", None)
            if isinstance(results, list):
                for r in results:
                    url = getattr(r, "url", None) or ""
                    if url:
                        web_sources.append({"title": getattr(r, "title", None) or "", "url": url})
    text = "".join(text_parts).strip()
    usage = getattr(msg, "usage", None)
    return {
        "persona_id": persona["id"], "label": persona["label"],
        "color": persona.get("color"),
        "text": text,
        "tokens": {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        },
        "web_sources": web_sources,
    }


SYNTH_INFERENCE_STRATEGIES = {"none", "reflection", "cove", "best_of_3"}


def _synth_call(
    client: Any, synth_model: str,
    system: str, user: str, web_grounding: bool, max_tokens: int = 1400,
) -> dict[str, Any]:
    """One synthesis LLM call. Returns {text, tokens, web_sources}."""
    kwargs: dict[str, Any] = {
        "model": synth_model,
        "max_tokens": max_tokens + 200 if web_grounding else max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_grounding:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "3")),
        }]
    try:
        msg = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        from graphify_runner import humanize_anthropic_error as _human
        return {"text": f"(synthesis failed: {_human(exc)})", "tokens": {"input": 0, "output": 0}, "web_sources": []}

    text_parts: list[str] = []
    web_sources: list[dict[str, str]] = []
    for b in msg.content:
        t = getattr(b, "type", None)
        if t == "text":
            text_parts.append(b.text)
        elif t == "web_search_tool_result":
            results = getattr(b, "content", None)
            if isinstance(results, list):
                for r in results:
                    url = getattr(r, "url", None) or ""
                    if url:
                        web_sources.append({"title": getattr(r, "title", None) or "", "url": url})
    text = "".join(text_parts).strip()
    usage = getattr(msg, "usage", None)
    return {
        "text": text,
        "tokens": {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        },
        "web_sources": web_sources,
    }


def _merge_tokens(*tokens: dict[str, Any] | None) -> dict[str, int]:
    total = {"input": 0, "output": 0}
    for t in tokens:
        if not t:
            continue
        total["input"] += int(t.get("input", 0) or 0)
        total["output"] += int(t.get("output", 0) or 0)
    return total


def _dedupe_sources(*lists: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for lst in lists:
        for s in lst or []:
            u = s.get("url")
            if u and u not in seen:
                seen.add(u)
                out.append(s)
    return out


def _synthesize(
    client: Any, synth_model: str,
    scenario: str, horizon_phrase: str, world_context: str,
    rounds_data: list[list[dict[str, Any]]],
    rubric_body: str,
    web_grounding: bool = False,
    intent_instruction: str = "",
    inference_strategy: str = "none",
) -> dict[str, Any]:
    """Synthesize the debate. Optionally run under an inference strategy:
    `none` (single pass), `reflection` (draft→critique→revise),
    `cove` (draft→verify→revise), `best_of_3` (3 samples→pick).
    Returns {text, tokens, web_sources, steps}.
    """
    intent_clause = f"\n\n### Conversation intent\n{intent_instruction}" if intent_instruction else ""
    rubric_clause = f"\n\nApply these framing rules:\n{rubric_body}" if rubric_body else ""
    base_system = (
        _HOUSE_STYLE
        + "\n\nYou synthesize a multi-agent debate into one forward-looking view. "
        + "Don't average — name the tension. Cite personas by label when it matters."
        + intent_clause
        + rubric_clause
    )

    rounds_block = []
    for ri, round_outputs in enumerate(rounds_data, start=1):
        round_lines = [f"## Round {ri}"]
        for entry in round_outputs:
            round_lines.append(f"### {entry['label']}\n{entry['text']}")
        rounds_block.append("\n\n".join(round_lines))
    transcript = "\n\n---\n\n".join(rounds_block)

    base_user = (
        f"Scenario: {scenario}\nHorizon: {horizon_phrase}\n"
        + (f"World context: {world_context}\n" if world_context else "")
        + f"\nDebate transcript:\n\n{transcript}\n\n"
        + "Produce Markdown — five sections, in this order, brief:\n\n"
        + "### Convergent claims\n3 bullets max. One claim per bullet, ≤ 18 words. "
          "Name the personas in parens.\n\n"
        + "### Divergent claims\n3 bullets max. Name which personas pull apart and the one-line crux.\n\n"
        + "### Position updates across rounds\nUp to 3 bullets — only real shifts. "
          "Omit the section entirely if nothing changed.\n\n"
        + "### Most-likely outcome\n**2 sentences max.** State the prediction; "
          "say which persona it leans on.\n\n"
        + "### Watch indicators\n3 bullets. Each = one observable signal in next 30–90 days. "
          "No commentary."
    )

    strategy = inference_strategy if inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"

    if strategy == "none":
        out = _synth_call(client, synth_model, base_system, base_user, web_grounding)
        return {**out, "steps": [{"label": "single-pass", "tokens": out.get("tokens", {})}]}

    if strategy == "reflection":
        draft = _synth_call(client, synth_model, base_system, base_user, web_grounding)
        critique_system = "You are a careful reviewer of multi-agent debate syntheses. Be specific and brief."
        critique_user = (
            f"Below is a synthesis of a foresight debate. Critique it:\n"
            f"- What's weak, missing, or unsupported?\n"
            f"- Are any 'convergent' claims actually only one persona?\n"
            f"- Do the watch indicators meet the bar (falsifiable, time-bounded, cheap to observe)?\n\n"
            f"Synthesis:\n{draft.get('text', '')}\n\n"
            f"Return 3-5 bullets, then a single line 'REVISE: yes' or 'REVISE: no'."
        )
        critique = _synth_call(client, synth_model, critique_system, critique_user, web_grounding=False, max_tokens=800)
        if "revise: no" in (critique.get("text", "") or "").lower():
            return {
                "text": draft.get("text", ""),
                "tokens": _merge_tokens(draft.get("tokens"), critique.get("tokens")),
                "web_sources": draft.get("web_sources", []),
                "steps": [
                    {"label": "draft", "tokens": draft.get("tokens", {})},
                    {"label": "critique (no revise)", "tokens": critique.get("tokens", {})},
                ],
            }
        revise_user = (
            f"Revise the synthesis to address the critique. Keep section structure. Concise.\n\n"
            f"Original synthesis:\n{draft.get('text', '')}\n\n"
            f"Critique:\n{critique.get('text', '')}\n\nRevised synthesis:"
        )
        revised = _synth_call(client, synth_model, base_system, revise_user, web_grounding=False)
        return {
            "text": revised.get("text", "") or draft.get("text", ""),
            "tokens": _merge_tokens(draft.get("tokens"), critique.get("tokens"), revised.get("tokens")),
            "web_sources": _dedupe_sources(draft.get("web_sources", []), revised.get("web_sources", [])),
            "steps": [
                {"label": "draft", "tokens": draft.get("tokens", {})},
                {"label": "critique", "tokens": critique.get("tokens", {})},
                {"label": "revise", "tokens": revised.get("tokens", {})},
            ],
        }

    if strategy == "cove":
        draft = _synth_call(client, synth_model, base_system, base_user, web_grounding)
        verify_user = (
            f"Generate 3-5 factual verification questions targeting specific claims in the synthesis "
            f"below. Then answer each one citing the debate transcript or marking 'uncertain'.\n\n"
            f"Synthesis:\n{draft.get('text', '')}\n\n"
            f"Format: Q1: ...\nA1: ...\nQ2: ..."
        )
        verify = _synth_call(client, synth_model, base_system, verify_user, web_grounding)
        revise_user = (
            f"Revise the synthesis using the verification answers. Remove or hedge claims that didn't "
            f"verify. Keep section structure.\n\n"
            f"Original synthesis:\n{draft.get('text', '')}\n\n"
            f"Verification:\n{verify.get('text', '')}\n\nRevised synthesis:"
        )
        revised = _synth_call(client, synth_model, base_system, revise_user, web_grounding=False)
        return {
            "text": revised.get("text", "") or draft.get("text", ""),
            "tokens": _merge_tokens(draft.get("tokens"), verify.get("tokens"), revised.get("tokens")),
            "web_sources": _dedupe_sources(
                draft.get("web_sources", []), verify.get("web_sources", []), revised.get("web_sources", []),
            ),
            "steps": [
                {"label": "draft", "tokens": draft.get("tokens", {})},
                {"label": "verify", "tokens": verify.get("tokens", {})},
                {"label": "revise", "tokens": revised.get("tokens", {})},
            ],
        }

    # best_of_3
    candidates = [
        _synth_call(client, synth_model, base_system, base_user, web_grounding) for _ in range(3)
    ]
    joined = "\n\n".join(f"### Candidate {i+1}\n{c.get('text', '')}" for i, c in enumerate(candidates))
    pick_user = (
        f"Three candidate syntheses of the same debate are below. Pick the BEST one based on:\n"
        f"- factual fidelity to the transcript\n"
        f"- whether convergent/divergent/position-updates sections are well-distinguished\n"
        f"- whether watch indicators are concrete and falsifiable\n\n"
        f"{joined}\n\n"
        f"Output ONLY the chosen candidate's full text (no preamble, no 'I chose Candidate X')."
    )
    picked = _synth_call(client, synth_model, base_system, pick_user, web_grounding=False)
    return {
        "text": picked.get("text", "") or candidates[0].get("text", ""),
        "tokens": _merge_tokens(*[c.get("tokens") for c in candidates], picked.get("tokens")),
        "web_sources": _dedupe_sources(*[c.get("web_sources", []) for c in candidates], picked.get("web_sources", [])),
        "steps": (
            [{"label": f"sample {i+1}", "tokens": c.get("tokens", {})} for i, c in enumerate(candidates)]
            + [{"label": "pick", "tokens": picked.get("tokens", {})}]
        ),
    }


def run_session(
    ws: Workspace,
    sid: str,
    client: Any,
    graph_context_fn: Any = None,
    rubric_body: str = "",
    memory_block: str = "",
    conversation_history: str = "",
    intent_instruction: str = "",
) -> dict[str, Any]:
    """Execute the full multi-round simulation for a session.

    graph_context_fn: optional callable(scenario, history_text="") -> {rendered, entry_node_labels}.
    memory_block: persistent-memory text to prepend to every persona's system prompt.
    conversation_history: rendered prior conversation turns to include in Round 1 context.
    intent_instruction: appended to the synthesizer's system prompt when the session is
      linked to a conversation that has an intent set.
    Returns the updated session with `output` populated.
    """
    session = get_session(ws, sid)
    if not session:
        return {"error": "session not found"}
    web_grounding = bool(session.get("web_grounding", False))
    synth_strategy = session.get("synth_inference_strategy", "none") or "none"

    personas = [get_persona(pid) for pid in session.get("personas", [])]
    personas = [p for p in personas if p]
    if not personas:
        return {"error": "no personas selected"}

    horizon_phrase = HORIZONS.get(session["horizon"], session["horizon"])
    scenario = session.get("scenario", "").strip()
    world_context = session.get("world_context", "").strip()

    graph_ctx = {"rendered": "", "entry_node_labels": []}
    if session.get("use_graph", True) and graph_context_fn:
        try:
            graph_ctx = graph_context_fn(scenario) or graph_ctx
        except Exception:
            pass

    # Track for the saved output so the UI can show what context was used.
    session.setdefault("output", {})
    used_memory = bool(memory_block and session.get("use_memory", True))
    used_conv = bool(conversation_history and session.get("source_conversation_id"))

    persona_model = session.get("answer_model") or os.environ.get(
        "GRAPHIFY_SIM_PERSONA_MODEL", "claude-haiku-4-5-20251001"
    )
    synth_model = os.environ.get("GRAPHIFY_SIM_SYNTH_MODEL", "claude-sonnet-4-6")

    effective_memory = memory_block if used_memory else ""
    effective_conv = conversation_history if used_conv else ""

    session["status"] = "running"
    session["output"] = {
        "rounds": [],
        "synthesis": "",
        "tokens": {"input": 0, "output": 0},
        "elapsed_ms": 0,
        "entry_node_labels": graph_ctx.get("entry_node_labels", []),
        "used_memory": used_memory,
        "used_conversation_history": used_conv,
        "used_web_grounding": web_grounding,
        "used_intent": bool(intent_instruction),
        "synth_inference_strategy": synth_strategy,
        "synth_inference_steps": [],
        "web_sources": [],
    }
    _save_session(ws, session)

    started = time.time()
    total_in = 0
    total_out = 0
    web_sources_all: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def _collect_web(entries: list[dict[str, Any]]) -> None:
        for r in entries:
            for s in r.get("web_sources", []) or []:
                u = s.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    web_sources_all.append(s)

    # ---- Round 1 (parallel) ----
    with ThreadPoolExecutor(max_workers=min(len(personas), 6)) as ex:
        futs = [
            ex.submit(
                _persona_first_round, client, persona_model, p,
                scenario, horizon_phrase, world_context,
                graph_ctx.get("rendered", ""), rubric_body,
                effective_memory, effective_conv, web_grounding,
            ) for p in personas
        ]
        round1 = [f.result() for f in futs]
    # Stable order matching the persona list.
    order_index = {p["id"]: i for i, p in enumerate(personas)}
    round1.sort(key=lambda r: order_index.get(r["persona_id"], 0))
    for r in round1:
        total_in += r.get("tokens", {}).get("input", 0)
        total_out += r.get("tokens", {}).get("output", 0)
    _collect_web(round1)
    session["output"]["rounds"].append(round1)
    _save_session(ws, session)

    # ---- Rounds 2..N (parallel, each persona sees others' prior outputs) ----
    for round_num in range(2, session["rounds"] + 1):
        prior_round = session["output"]["rounds"][-1]
        by_persona = {entry["persona_id"]: entry for entry in prior_round}

        def reaction_for(p: dict[str, Any]) -> dict[str, Any]:
            own_prior = by_persona.get(p["id"], {}).get("text", "")
            others_prior = [
                {"label": entry["label"], "text": entry["text"]}
                for entry in prior_round if entry["persona_id"] != p["id"]
            ]
            return _persona_reaction_round(
                client, persona_model, p,
                scenario, horizon_phrase, world_context,
                own_prior, others_prior, rubric_body, round_num,
                memory_block=effective_memory,
                web_grounding=web_grounding,
            )

        with ThreadPoolExecutor(max_workers=min(len(personas), 6)) as ex:
            futs = [ex.submit(reaction_for, p) for p in personas]
            this_round = [f.result() for f in futs]
        this_round.sort(key=lambda r: order_index.get(r["persona_id"], 0))
        for r in this_round:
            total_in += r.get("tokens", {}).get("input", 0)
            total_out += r.get("tokens", {}).get("output", 0)
        _collect_web(this_round)
        session["output"]["rounds"].append(this_round)
        _save_session(ws, session)

    # ---- Synthesis ----
    synth = _synthesize(
        client, synth_model, scenario, horizon_phrase, world_context,
        session["output"]["rounds"], rubric_body,
        web_grounding=web_grounding,
        intent_instruction=intent_instruction,
        inference_strategy=synth_strategy,
    )
    session["output"]["synthesis"] = synth["text"]
    session["output"]["synth_inference_steps"] = synth.get("steps", [])
    total_in += synth["tokens"].get("input", 0)
    total_out += synth["tokens"].get("output", 0)
    for s in synth.get("web_sources", []) or []:
        u = s.get("url")
        if u and u not in seen_urls:
            seen_urls.add(u)
            web_sources_all.append(s)

    session["output"]["tokens"] = {"input": total_in, "output": total_out}
    session["output"]["elapsed_ms"] = int((time.time() - started) * 1000)
    session["output"]["web_sources"] = web_sources_all
    session["status"] = "complete"
    _save_session(ws, session)
    return session
