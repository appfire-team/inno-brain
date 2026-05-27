"""Typed artifacts — first-class outputs of playbook runs.

An Artifact is the durable output of a Playbook. It carries the LLM-generated
brief in two shapes:

  - `tldr` + `sections` (structured, machine-readable)
  - `raw_markdown`     (human-readable, ready for export)

Plus `provenance`: which playbook run produced it, what source artifacts (if
any) it composes from, the scenario the user kicked off with. This lets later
playbooks ingest earlier artifacts as input — e.g. a `PRDDraft` can be seeded
from an `OpportunityScan`.

Storage: one JSON file per artifact under data/workspaces/<id>/artifacts/.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from workspaces import Workspace


# Known artifact types. Each playbook emits exactly one type; the frontend
# uses this for icons and to filter the source-artifact picker.
ARTIFACT_TYPES: dict[str, str] = {
    # Playbook-emitted briefs
    "OpportunityScan": "Product opportunity scan",
    "StrategyBrief": "Strategy brief",
    "BuildBuyDecision": "Build/Buy/Partner decision",
    "PRDDraft": "PRD draft",
    "LaunchPlan": "Launch plan (GTM)",
    "CodebaseAudit": "Codebase audit",
    "KBHealthReport": "KB freshness audit",
    # Free-form artifacts pushed in from Conversations / ForeSight / manual
    "ConversationNote": "Conversation note",
    "ConversationReport": "Conversation report",
    "ForesightBrief": "Foresight brief",
    "FreeformNote": "Free-form note",
    # Brownfield AI-led development chain (agent-skills grounded)
    "IdeaRefinement": "Idea refinement",
    "PRD": "Product requirements (PRD)",
    "ArchitectureDoc": "Architecture document",
    "DeliveryPlan": "Delivery plan",
    "DeliveryReport": "Delivery report",
    "SecurityReview": "Security review",
    "TestPlan": "Test plan",
}


def _safe_id(art_id: str) -> str:
    safe = "".join(c for c in art_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("Invalid artifact id")
    return safe


def _path(ws: Workspace, art_id: str) -> Path:
    ws.ensure_dirs()
    return ws.artifacts_dir / f"{_safe_id(art_id)}.json"


def _save(ws: Workspace, art: dict[str, Any]) -> None:
    _path(ws, art["id"]).write_text(json.dumps(art, indent=2))


def list_artifacts(ws: Workspace, *, artifact_type: str | None = None) -> list[dict[str, Any]]:
    """List artifacts. Optionally filter by type."""
    out: list[dict[str, Any]] = []
    if not ws.artifacts_dir.exists():
        return out
    for p in sorted(ws.artifacts_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            a = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if artifact_type and a.get("type") != artifact_type:
            continue
        # Return a lightweight summary; the full object is fetched via get_artifact.
        out.append({
            "id": a["id"],
            "type": a.get("type"),
            "title": a.get("title", "Untitled"),
            "tldr": a.get("tldr", ""),
            "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"),
            "playbook_id": a.get("provenance", {}).get("playbook_id"),
            "playbook_run_id": a.get("provenance", {}).get("playbook_run_id"),
            "source_artifact_ids": a.get("provenance", {}).get("source_artifact_ids", []),
        })
    return out


def get_artifact(ws: Workspace, art_id: str) -> dict[str, Any] | None:
    p = _path(ws, art_id)
    if not p.exists():
        return None
    art = json.loads(p.read_text())
    return _ensure_review_fields(art)


def create_artifact(
    ws: Workspace,
    *,
    artifact_type: str,
    title: str,
    tldr: str,
    sections: dict[str, str] | None = None,
    raw_markdown: str,
    provenance: dict[str, Any] | None = None,
    highlights: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Persist a new artifact. `sections` is the typed body; `raw_markdown` is the
    full human-readable rendering (usually = headers + sections concatenated).

    `highlights` is an optional list of {text, tone} chips surfaced above the
    sections in the UI — used by the magazine layout to give a 30-second skim.

    Top-level `tldr` / `sections` / `raw_markdown` always mirror the current
    version's body — every existing reader (render_for_prompt, the source-artifact
    picker, the run view) sees the latest revision without knowing about versions.
    The `versions` array is the immutable history.
    """
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"Unknown artifact type: {artifact_type}")
    now = time.time()
    initial_sections = sections or {}
    initial_tldr = tldr.strip()
    initial_highlights = highlights or []
    art = {
        "id": uuid.uuid4().hex[:12],
        "type": artifact_type,
        "title": title.strip() or ARTIFACT_TYPES[artifact_type],
        "tldr": initial_tldr,
        "sections": initial_sections,
        "highlights": initial_highlights,
        "raw_markdown": raw_markdown,
        "provenance": provenance or {},
        "created_at": now,
        "updated_at": now,
        "current_version": 1,
        "versions": [{
            "v": 1,
            "tldr": initial_tldr,
            "sections": initial_sections,
            "highlights": initial_highlights,
            "raw_markdown": raw_markdown,
            "created_at": now,
            "summary": "Initial draft",
        }],
        "comments": [],
    }
    _save(ws, art)
    return art


def delete_artifact(ws: Workspace, art_id: str) -> bool:
    p = _path(ws, art_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def rename_artifact(ws: Workspace, art_id: str, title: str) -> dict[str, Any] | None:
    """Patch an artifact's display title. Returns the updated artifact, or None
    when the id doesn't exist. Empty/whitespace titles are rejected — callers
    should use the prior title in that case."""
    art = get_artifact(ws, art_id)
    if not art:
        return None
    new_title = (title or "").strip()
    if not new_title:
        raise ValueError("Title cannot be empty")
    art["title"] = new_title
    art["updated_at"] = time.time()
    _save(ws, art)
    return art


def render_for_prompt(art: dict[str, Any]) -> str:
    """Render an artifact as a compact context block for injection into a
    downstream playbook's first-step prompt. Keeps tldr + sections; omits the
    full raw_markdown to bound tokens.

    Reads from the top-level `tldr`/`sections`, which always mirror the
    current version — so chained playbooks ingest the latest revision.
    """
    lines = [
        f"## Prior artifact: {art.get('title', '')} ({ARTIFACT_TYPES.get(art.get('type', ''), art.get('type'))})",
        f"**TL;DR:** {art.get('tldr', '')}",
    ]
    sections = art.get("sections") or {}
    for k, v in sections.items():
        text = (v or "").strip()
        if not text:
            continue
        lines.append(f"\n### {k}\n{text}")
    return "\n".join(lines)


# ---------- Comments & versions -------------------------------------------

def _ensure_review_fields(art: dict[str, Any]) -> dict[str, Any]:
    """Backfill review fields on artifacts created before this feature shipped."""
    if "comments" not in art:
        art["comments"] = []
    if "versions" not in art:
        art["versions"] = [{
            "v": 1,
            "tldr": art.get("tldr", ""),
            "sections": art.get("sections", {}),
            "raw_markdown": art.get("raw_markdown", ""),
            "created_at": art.get("created_at", time.time()),
            "summary": "Initial draft",
        }]
    if "current_version" not in art:
        art["current_version"] = art["versions"][-1]["v"]
    return art


def add_comment(
    ws: Workspace,
    art_id: str,
    *,
    text: str,
    author: str = "",
    section: str | None = None,
) -> dict[str, Any] | None:
    art = get_artifact(ws, art_id)
    if not art:
        return None
    _ensure_review_fields(art)
    text = (text or "").strip()
    if not text:
        raise ValueError("Comment text is required")
    comment = {
        "id": uuid.uuid4().hex[:10],
        "author": (author or "").strip(),
        "section": section.strip() if isinstance(section, str) and section.strip() else None,
        "text": text,
        "status": "open",
        "created_at": time.time(),
        "addressed_in_version": None,
    }
    art["comments"].append(comment)
    art["updated_at"] = time.time()
    _save(ws, art)
    return comment


def update_comment(
    ws: Workspace,
    art_id: str,
    comment_id: str,
    *,
    status: str | None = None,
    text: str | None = None,
) -> dict[str, Any] | None:
    art = get_artifact(ws, art_id)
    if not art:
        return None
    _ensure_review_fields(art)
    for c in art["comments"]:
        if c["id"] != comment_id:
            continue
        if status is not None:
            if status not in ("open", "addressed", "resolved"):
                raise ValueError(f"Invalid status: {status}")
            c["status"] = status
        if text is not None:
            t = text.strip()
            if not t:
                raise ValueError("Comment text cannot be empty")
            c["text"] = t
        art["updated_at"] = time.time()
        _save(ws, art)
        return c
    return None


def add_version(
    ws: Workspace,
    art_id: str,
    *,
    tldr: str,
    sections: dict[str, str],
    raw_markdown: str,
    summary: str = "",
    addressed_comment_ids: list[str] | None = None,
    highlights: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Snapshot a new version of an artifact (typically written by refine).

    Top-level body is overwritten to mirror the new version. Comments in
    `addressed_comment_ids` are flipped from `open` to `addressed` and tagged
    with the version that handled them — reviewers manually move them to
    `resolved` once they've verified the revision.

    If `highlights` is omitted, the prior version's highlights carry forward —
    refine cycles touch sections, not the headline strip.
    """
    art = get_artifact(ws, art_id)
    if not art:
        return None
    _ensure_review_fields(art)
    now = time.time()
    next_v = (art["versions"][-1]["v"] if art["versions"] else 0) + 1
    effective_highlights = (
        highlights if highlights is not None else (art.get("highlights") or [])
    )
    version_entry = {
        "v": next_v,
        "tldr": tldr,
        "sections": sections,
        "highlights": effective_highlights,
        "raw_markdown": raw_markdown,
        "created_at": now,
        "summary": summary or f"Revision v{next_v}",
    }
    art["versions"].append(version_entry)
    art["current_version"] = next_v
    art["tldr"] = tldr
    art["sections"] = sections
    art["highlights"] = effective_highlights
    art["raw_markdown"] = raw_markdown
    art["updated_at"] = now

    addressed = set(addressed_comment_ids or [])
    if addressed:
        for c in art["comments"]:
            if c["id"] in addressed and c["status"] == "open":
                c["status"] = "addressed"
                c["addressed_in_version"] = next_v

    _save(ws, art)
    return art


# ---------- Q&A + simplification ------------------------------------------

def _render_artifact_for_qa(art: dict[str, Any]) -> str:
    """Render an artifact as a rich context block for follow-up Q&A — includes
    the full prose + the run's per-step outputs when available."""
    parts = [
        f"# {art.get('title', 'Brief')}",
        f"\n**TL;DR:** {art.get('tldr', '')}",
    ]
    for h in art.get("highlights") or []:
        parts.append(f"- ({h.get('tone', 'claim')}) {h.get('text', '')}")
    for k, v in (art.get("sections") or {}).items():
        text = (v or "").strip()
        if not text:
            continue
        parts.append(f"\n## {k}\n{text}")
    return "\n".join(parts)


def _load_run_transcript(ws: Workspace, art: dict[str, Any]) -> str:
    """Best-effort: include the run's per-step outputs so Q&A has access to
    the reasoning behind the brief, not just the final prose. Empty if the
    run is missing (deleted, never persisted)."""
    run_id = (art.get("provenance") or {}).get("playbook_run_id")
    if not run_id:
        return ""
    try:
        import playbooks  # local import to avoid load-time cycle
        run = playbooks.get_run(ws, run_id)
    except Exception:
        return ""
    if not run:
        return ""
    blocks: list[str] = []
    for s in run.get("steps", []):
        out = (s.get("output") or "").strip()
        if not out:
            continue
        blocks.append(f"### Step: {s.get('label', s.get('id', ''))}\n{out}")
    return "\n\n---\n\n".join(blocks)


def ask_artifact(
    ws: Workspace, art_id: str, question: str, *,
    answer_model: str | None = None,
) -> dict[str, Any] | None:
    """Run a follow-up Q&A turn grounded in the artifact + (if available) the
    full per-step transcript of its source run. Persists the Q&A entry onto
    the artifact under `qa_history` so the panel reads natively. Returns the
    saved entry, or None if the artifact is missing.
    """
    art = get_artifact(ws, art_id)
    if not art:
        return None
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")

    from graphify_runner import _anthropic_client
    import os as _os
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured (ANTHROPIC_API_KEY missing)")

    artifact_block = _render_artifact_for_qa(art)
    transcript = _load_run_transcript(ws, art)
    prior_qa = art.get("qa_history") or []
    qa_thread = "\n\n".join(
        f"Q: {q.get('question', '')}\nA: {q.get('answer', '')}"
        for q in prior_qa[-5:]  # last 5 turns of context
    )

    system = (
        "You are a research analyst answering follow-up questions about a "
        "completed playbook brief. Ground every answer in the brief and the "
        "per-step transcript below — cite section names when relevant. If "
        "the answer isn't supported by the materials, say so explicitly "
        "rather than speculating. Keep answers concrete and bounded — one "
        "to four short paragraphs unless the question genuinely demands more."
    )

    user_parts = [f"## Brief\n{artifact_block}"]
    if transcript:
        user_parts.append(
            f"## Per-step transcript (the reasoning behind the brief)\n{transcript[:20000]}"
        )
    if qa_thread:
        user_parts.append(f"## Prior follow-ups in this thread\n{qa_thread}")
    user_parts.append(f"## New question\n{question}")
    user = "\n\n".join(user_parts)

    model = (answer_model or "").strip() or _os.environ.get(
        "GRAPHIFY_PLAYBOOK_MODEL", "claude-sonnet-4-6"
    )
    with client.messages.stream(
        model=model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    usage = getattr(msg, "usage", None)
    tokens = {
        "input": getattr(usage, "input_tokens", 0) if usage else 0,
        "output": getattr(usage, "output_tokens", 0) if usage else 0,
    }

    entry = {
        "id": uuid.uuid4().hex[:10],
        "question": question,
        "answer": text,
        "tokens": tokens,
        "created_at": time.time(),
    }
    art.setdefault("qa_history", []).append(entry)
    art["updated_at"] = time.time()
    _save(ws, art)
    return entry


def simplify_artifact(
    ws: Workspace, art_id: str, *,
    force: bool = False,
    answer_model: str | None = None,
) -> dict[str, Any] | None:
    """Generate a plain-language rewrite of the artifact for non-expert
    readers. Caches the result on the artifact (`simplified`) so subsequent
    reads are free; pass `force=True` to regenerate (e.g. after edits).
    Returns {body, tokens, created_at} or None if the artifact is missing.
    """
    art = get_artifact(ws, art_id)
    if not art:
        return None

    if not force:
        cached = art.get("simplified")
        if cached and cached.get("body"):
            # Stale if produced before the artifact's current version. Use
            # updated_at as the freshness check.
            if cached.get("source_updated_at") == art.get("updated_at"):
                return cached

    from graphify_runner import _anthropic_client
    import os as _os
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured (ANTHROPIC_API_KEY missing)")

    artifact_block = _render_artifact_for_qa(art)
    system = (
        "Rewrite the strategy brief below for a non-expert reader who is "
        "smart but unfamiliar with the company's jargon, named products, "
        "or competitive landscape. Goals:\n\n"
        "- Use everyday language. No acronyms without expansion. No insider "
        "names without a one-line gloss (e.g. 'Comala — Appfire's tool for "
        "tracking document approvals in Confluence').\n"
        "- Preserve every concrete number (ARR, dates, costs, kill gates).\n"
        "- Keep the same section structure (TL;DR, then each section in "
        "order) so a reader can compare side-by-side with the original.\n"
        "- Bullets and short sentences over dense paragraphs.\n"
        "- Don't soften strategic verdicts. If the brief recommends a bet, "
        "the rewrite recommends the same bet, just clearly.\n\n"
        "Output Markdown only — no preamble, no meta-commentary."
    )
    user = (
        "Rewrite the following strategy brief in plain language for a smart "
        "non-expert. Keep the structure; lose the jargon.\n\n"
        f"{artifact_block}"
    )
    model = (answer_model or "").strip() or _os.environ.get(
        "GRAPHIFY_PLAYBOOK_MODEL", "claude-sonnet-4-6"
    )
    with client.messages.stream(
        model=model,
        max_tokens=12000,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    body = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    usage = getattr(msg, "usage", None)
    tokens = {
        "input": getattr(usage, "input_tokens", 0) if usage else 0,
        "output": getattr(usage, "output_tokens", 0) if usage else 0,
    }
    simplified = {
        "body": body,
        "tokens": tokens,
        "created_at": time.time(),
        "source_updated_at": art.get("updated_at"),
    }
    art["simplified"] = simplified
    art["updated_at"] = time.time()
    _save(ws, art)
    return simplified


def delete_qa_entry(ws: Workspace, art_id: str, qa_id: str) -> bool:
    art = get_artifact(ws, art_id)
    if not art:
        return False
    qa = art.get("qa_history") or []
    new_qa = [q for q in qa if q.get("id") != qa_id]
    if len(new_qa) == len(qa):
        return False
    art["qa_history"] = new_qa
    art["updated_at"] = time.time()
    _save(ws, art)
    return True
