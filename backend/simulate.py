"""Lightweight multi-agent scenario simulation.

Given a question + a time horizon, 4 fixed personas each give a short prediction
from their viewpoint, in parallel. A synthesizer then reconciles them into a
single forward-looking view with convergent claims, divergent claims, the most
likely outcome, and 2-3 watch indicators.

The output is persisted as a special "simulation" turn in the conversation so
the team can revisit it.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Fixed persona roster. Keep this small and recognisable; rubric/intent on the
# host conversation already adds company-specific framing, so personas only
# need to capture the *viewpoint*.
PERSONAS: dict[str, dict[str, str]] = {
    "bull": {
        "label": "Bull",
        "tagline": "what goes right",
        "system": (
            "You are the BULL voice in a scenario simulation. Argue for the most plausible "
            "upside path. Identify what has to go right, who benefits, and the leading signal "
            "you'd watch. Be concrete, not cheerleader-ish. 4-6 sentences."
        ),
    },
    "bear": {
        "label": "Bear",
        "tagline": "what goes wrong",
        "system": (
            "You are the BEAR voice in a scenario simulation. Argue the strongest downside "
            "case. Identify the specific failure modes, what makes them likely, and the earliest "
            "indicator you'd see them coming. Be specific, not generic doom. 4-6 sentences."
        ),
    },
    "customer": {
        "label": "Customer",
        "tagline": "voice of the buyer",
        "system": (
            "You are the CUSTOMER voice in a scenario simulation. Speak as the buyer or end-user "
            "who'd actually pay for the outcome. What changes for them? What's their alternative? "
            "When would they say 'no thanks'? 4-6 sentences."
        ),
    },
    "competitor": {
        "label": "Competitor",
        "tagline": "what the field does in response",
        "system": (
            "You are the COMPETITOR voice in a scenario simulation. You run a rival company or "
            "the incumbent. How do you respond? What's your strongest counter? What's the single "
            "move you'd make to neutralise this? 4-6 sentences."
        ),
    },
}

HORIZONS: dict[str, str] = {
    "6mo": "6 months from now",
    "1y": "1 year from now",
    "3y": "3 years from now",
}


def _call_persona(
    client: Any,
    model: str,
    persona_key: str,
    question: str,
    horizon: str,
    graph_context: str,
    history_text: str,
    rubric_body: str,
    memory_block: str = "",
    web_grounding: bool = False,
) -> dict[str, Any]:
    persona = PERSONAS[persona_key]
    horizon_phrase = HORIZONS.get(horizon, horizon)
    system_parts: list[str] = []
    if memory_block:
        system_parts.append(memory_block)
    system_parts.append(persona["system"])
    if rubric_body:
        system_parts.append("Apply these framing rules:\n" + rubric_body)
    if web_grounding:
        system_parts.append(
            "You have a web_search tool. Use it ONLY to verify time-sensitive facts "
            "(dates, deadlines, current product/company status, regulations) the corpus "
            "is silent on. Cite as 'web: domain.com'."
        )
    system = "\n\n".join(system_parts)

    user_blocks = [
        f"Scenario question: {question}",
        f"Time horizon: {horizon_phrase}",
    ]
    if history_text:
        user_blocks.append(f"Prior conversation context:\n{history_text}")
    if graph_context:
        user_blocks.append(f"Relevant knowledge graph context:\n{graph_context}")
    user_blocks.append(
        f"Speak only as the {persona['label']} voice. Do not summarise other viewpoints. "
        "Output prose, no headers. End with one line starting with 'WATCH: ' naming the single "
        "earliest indicator that would tell us your scenario is playing out."
    )
    user = "\n\n".join(user_blocks)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 800 if web_grounding else 600,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_grounding:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "2")),
        }]
    try:
        msg = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "key": persona_key, "label": persona["label"],
            "text": f"(failed: {exc})", "tokens": {}, "web_sources": [],
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
        "key": persona_key,
        "label": persona["label"],
        "tagline": persona["tagline"],
        "text": text,
        "tokens": {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        },
        "web_sources": web_sources,
    }


SYNTH_INFERENCE_STRATEGIES = {"none", "reflection", "cove", "best_of_3"}


def _synth_call(
    client: Any, model: str, system: str, user: str,
    web_grounding: bool, max_tokens: int = 1500,
) -> dict[str, Any]:
    """One synthesis LLM call. Returns {text, tokens, web_sources}."""
    kwargs: dict[str, Any] = {
        "model": model,
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
        return {"text": f"(synthesis failed: {exc})", "tokens": {"input": 0, "output": 0}, "web_sources": []}

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


def _synthesize_personas(
    client: Any,
    model: str,
    question: str,
    horizon: str,
    persona_results: list[dict[str, Any]],
    rubric_body: str,
    web_grounding: bool = False,
    intent_instruction: str = "",
    inference_strategy: str = "none",
) -> dict[str, Any]:
    horizon_phrase = HORIZONS.get(horizon, horizon)
    blocks = [f"### {r['label']}\n{r['text']}" for r in persona_results]
    joined = "\n\n".join(blocks)

    intent_clause = f"\n\n### Conversation intent\n{intent_instruction}" if intent_instruction else ""
    rubric_clause = f"\n\nApply these framing rules:\n{rubric_body}" if rubric_body else ""
    base_system = (
        "You synthesize a multi-agent scenario simulation into a single forward-looking view. "
        "Be honest about disagreement. Don't average — name the tension."
        + intent_clause
        + rubric_clause
    )
    base_user = (
        f"Scenario question: {question}\nHorizon: {horizon_phrase}\n\n"
        f"Personas:\n\n{joined}\n\n"
        "Produce Markdown with exactly these four sections:\n"
        "### Convergent claims\n(things 2+ personas agree on — bulleted)\n\n"
        "### Divergent claims\n(where personas pull apart — bulleted, each line "
        "identifying which personas disagree and why)\n\n"
        "### Most-likely outcome\n(your best single prediction in 2-4 sentences, "
        "explicitly noting which persona's view it leans toward and why)\n\n"
        "### Watch indicators\n(2-3 bullets — concrete, observable signals that would "
        "tell us which scenario is playing out in the next 30-90 days)"
    )

    strategy = inference_strategy if inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"

    if strategy == "none":
        out = _synth_call(client, model, base_system, base_user, web_grounding)
        return {**out, "steps": [{"label": "single-pass", "tokens": out.get("tokens", {})}]}

    if strategy == "reflection":
        draft = _synth_call(client, model, base_system, base_user, web_grounding)
        critique_system = "You are a careful reviewer of scenario syntheses. Be specific and brief."
        critique_user = (
            f"Below is a synthesis of a quick scenario simulation. Critique it:\n"
            f"- What's weak, missing, or unsupported?\n"
            f"- Are any 'convergent' claims actually only one persona?\n"
            f"- Are the watch indicators concrete, falsifiable, time-bounded?\n\n"
            f"Synthesis:\n{draft.get('text','')}\n\n"
            f"Return 3-5 bullets, then a single line 'REVISE: yes' or 'REVISE: no'."
        )
        critique = _synth_call(client, model, critique_system, critique_user, web_grounding=False, max_tokens=700)
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
            f"Revise the synthesis to address the critique. Keep the four-section structure. Concise.\n\n"
            f"Original:\n{draft.get('text','')}\n\nCritique:\n{critique.get('text','')}\n\nRevised:"
        )
        revised = _synth_call(client, model, base_system, revise_user, web_grounding=False)
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
        draft = _synth_call(client, model, base_system, base_user, web_grounding)
        verify_user = (
            f"Generate 3-5 factual verification questions targeting specific claims in the synthesis "
            f"below. Then answer each one citing the persona outputs or marking 'uncertain'.\n\n"
            f"Synthesis:\n{draft.get('text','')}\n\nFormat: Q1: ...\nA1: ...\nQ2: ..."
        )
        verify = _synth_call(client, model, base_system, verify_user, web_grounding)
        revise_user = (
            f"Revise the synthesis using the verification answers. Remove or hedge claims that didn't "
            f"verify. Keep section structure.\n\nOriginal:\n{draft.get('text','')}\n\n"
            f"Verification:\n{verify.get('text','')}\n\nRevised:"
        )
        revised = _synth_call(client, model, base_system, revise_user, web_grounding=False)
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
    candidates = [_synth_call(client, model, base_system, base_user, web_grounding) for _ in range(3)]
    joined_candidates = "\n\n".join(f"### Candidate {i+1}\n{c.get('text','')}" for i, c in enumerate(candidates))
    pick_user = (
        f"Three candidate syntheses of the same simulation are below. Pick the BEST one based on:\n"
        f"- factual fidelity to the persona outputs\n"
        f"- clarity of convergent vs divergent claims\n"
        f"- whether watch indicators are concrete and falsifiable\n\n"
        f"{joined_candidates}\n\n"
        f"Output ONLY the chosen candidate's full text (no preamble, no 'I chose Candidate X')."
    )
    picked = _synth_call(client, model, base_system, pick_user, web_grounding=False)
    return {
        "text": picked.get("text", "") or candidates[0].get("text", ""),
        "tokens": _merge_tokens(*[c.get("tokens") for c in candidates], picked.get("tokens")),
        "web_sources": _dedupe_sources(*[c.get("web_sources", []) for c in candidates], picked.get("web_sources", [])),
        "steps": (
            [{"label": f"sample {i+1}", "tokens": c.get("tokens", {})} for i, c in enumerate(candidates)]
            + [{"label": "pick", "tokens": picked.get("tokens", {})}]
        ),
    }


def run_simulation(
    client: Any,
    question: str,
    horizon: str,
    graph_context: str = "",
    history_text: str = "",
    rubric_body: str = "",
    persona_keys: list[str] | None = None,
    memory_block: str = "",
    web_grounding: bool = False,
    intent_instruction: str = "",
    inference_strategy: str = "none",
) -> dict[str, Any]:
    """Run all 4 personas in parallel, then synthesize. Returns one persisted-ready dict."""
    persona_keys = persona_keys or list(PERSONAS.keys())
    persona_model = os.environ.get("GRAPHIFY_SIM_PERSONA_MODEL", "claude-haiku-4-5-20251001")
    synth_model = os.environ.get("GRAPHIFY_SIM_SYNTH_MODEL", "claude-sonnet-4-6")

    started = time.time()
    persona_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(persona_keys), 4)) as ex:
        futures = {
            ex.submit(
                _call_persona,
                client, persona_model, key, question, horizon,
                graph_context, history_text, rubric_body,
                memory_block, web_grounding,
            ): key
            for key in persona_keys
        }
        for fut in futures:
            persona_results.append(fut.result())

    # Keep the personas in stable order matching persona_keys.
    persona_results.sort(key=lambda r: persona_keys.index(r["key"]))

    synth = _synthesize_personas(
        client, synth_model, question, horizon, persona_results, rubric_body,
        web_grounding=web_grounding,
        intent_instruction=intent_instruction,
        inference_strategy=inference_strategy,
    )

    total_in = sum(r.get("tokens", {}).get("input", 0) for r in persona_results) + synth.get("tokens", {}).get("input", 0)
    total_out = sum(r.get("tokens", {}).get("output", 0) for r in persona_results) + synth.get("tokens", {}).get("output", 0)

    # Dedupe web_sources across personas + synth for the saved turn.
    seen_urls: set[str] = set()
    all_web_sources: list[dict[str, str]] = []
    for src_list in [r.get("web_sources", []) for r in persona_results] + [synth.get("web_sources", [])]:
        for s in src_list or []:
            url = s.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_sources.append(s)

    strategy_used = inference_strategy if inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"
    return {
        "kind": "simulation",
        "question": question,
        "horizon": horizon,
        "horizon_label": HORIZONS.get(horizon, horizon),
        "personas": persona_results,
        "synthesis": synth["text"],
        "tokens": {"input": total_in, "output": total_out},
        "elapsed_ms": int((time.time() - started) * 1000),
        "used_memory": bool(memory_block),
        "used_web_grounding": bool(web_grounding),
        "used_intent": bool(intent_instruction),
        "synth_inference_strategy": strategy_used,
        "synth_inference_steps": synth.get("steps", []),
        "web_sources": all_web_sources,
    }
