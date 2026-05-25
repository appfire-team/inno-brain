"""KB refinement layer — corrections, additions, attestations, dissents.

The graph file built by `graphify` is treated as a read-only derivation of the
ingested documents. Refinements live in a separate per-workspace store and are
merged into the subgraph at read time by `apply_corrections`. Removing a
refinement instantly restores the original extraction (the graph is untouched).

Four kinds of refinements:

- "correction"  — the graph fact is wrong; replace summary with `new_summary`.
- "addition"    — a new fact not in the graph (no target_node_id needed).
- "attestation" — someone with standing vouches for the existing fact.
- "dissent"     — challenges the fact without overriding (surfaced alongside).

Schema:
  {
    "id": "<hex>",
    "kind": "correction" | "addition" | "attestation" | "dissent",
    "target_node_id": str | null,    # null for pure additions
    "target_edge_id": str | null,    # optional: target a relationship
    "source_type": "human" | "document" | "web" | "kb_audit",
    "author": str,                   # who asserted it
    "author_basis": str,             # why their assertion should be weighted
    "confidence": "high" | "medium" | "low",
    "original_summary": str,         # for correction/attestation/dissent: what the graph said
    "new_summary": str,              # for correction/addition: the new claim
    "reason": str,                   # short prose explaining the change
    "evidence_url": str | null,
    "created_at": float,
    "updated_at": float,
  }

Corrections are workspace-scoped: a fact's truth often depends on which lens
the workspace is looking through (e.g. "Appfire's marketplace KB" vs "a
competitor's marketplace KB").
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from workspaces import Workspace


VALID_KINDS = {"correction", "addition", "attestation", "dissent"}
VALID_SOURCE_TYPES = {"human", "document", "web", "kb_audit"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def _dir(ws: Workspace) -> Path:
    ws.ensure_dirs()
    d = ws.path / "kb_corrections"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ws: Workspace, cid: str) -> Path:
    safe = "".join(c for c in cid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid correction id")
    return _dir(ws) / f"{safe}.json"


def _save(ws: Workspace, c: dict[str, Any]) -> None:
    _path(ws, c["id"]).write_text(json.dumps(c, indent=2))


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload.get("kind") or "correction"
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind: {kind}")
    source_type = payload.get("source_type") or "human"
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"invalid source_type: {source_type}")
    confidence = payload.get("confidence") or "medium"
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"invalid confidence: {confidence}")
    # Per-kind required fields. Keep this loose — UI does the heavy lifting.
    if kind in ("correction", "attestation", "dissent") and not payload.get("target_node_id"):
        raise ValueError(f"{kind} requires target_node_id")
    if kind in ("correction", "addition") and not (payload.get("new_summary") or "").strip():
        raise ValueError(f"{kind} requires new_summary")
    return {
        "kind": kind,
        "target_node_id": (payload.get("target_node_id") or None),
        "target_edge_id": (payload.get("target_edge_id") or None),
        "source_type": source_type,
        "author": (payload.get("author") or "").strip(),
        "author_basis": (payload.get("author_basis") or "").strip(),
        "confidence": confidence,
        "original_summary": (payload.get("original_summary") or "").strip(),
        "new_summary": (payload.get("new_summary") or "").strip(),
        "reason": (payload.get("reason") or "").strip(),
        "evidence_url": (payload.get("evidence_url") or "").strip() or None,
    }


def list_corrections(ws: Workspace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    d = ws.path / "kb_corrections"
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def get_correction(ws: Workspace, cid: str) -> dict[str, Any] | None:
    p = _path(ws, cid)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def create_correction(ws: Workspace, payload: dict[str, Any]) -> dict[str, Any]:
    fields = _normalize(payload)
    now = time.time()
    cid = uuid.uuid4().hex[:10]
    record = {"id": cid, **fields, "created_at": now, "updated_at": now}
    _save(ws, record)
    return record


def update_correction(ws: Workspace, cid: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_correction(ws, cid)
    if not existing:
        return None
    merged = {**existing}
    # Allow partial patches: only override fields that are present in the payload.
    for k in ("kind", "target_node_id", "target_edge_id", "source_type", "author",
              "author_basis", "confidence", "original_summary", "new_summary",
              "reason", "evidence_url"):
        if k in payload and payload[k] is not None:
            merged[k] = payload[k]
    # Re-normalize the merged record to catch invalid transitions (e.g. dropping
    # target_node_id from a correction).
    normalized = _normalize(merged)
    record = {**existing, **normalized, "updated_at": time.time()}
    _save(ws, record)
    return record


def delete_correction(ws: Workspace, cid: str) -> bool:
    p = _path(ws, cid)
    if not p.exists():
        return False
    p.unlink()
    return True


def index_by_target(ws: Workspace) -> dict[str, list[dict[str, Any]]]:
    """Group corrections by target_node_id. Used by apply_corrections so we can
    do one O(N) pass over a subgraph instead of N lookups."""
    out: dict[str, list[dict[str, Any]]] = {}
    for c in list_corrections(ws):
        target = c.get("target_node_id")
        if target:
            out.setdefault(target, []).append(c)
    return out


# ---- apply_corrections ----------------------------------------------------
# Merge the corrections layer onto a rendered subgraph string that's about to
# go to the LLM. We deliberately operate on the rendered text (not the graph
# object) so this works uniformly across conversations, playbooks, and
# foresight — they all funnel through a rendered subgraph block.

def apply_corrections_to_subgraph(
    rendered_subgraph: str,
    ws: Workspace,
    *,
    node_id_to_label: dict[str, str] | None = None,
) -> str:
    """Return `rendered_subgraph` with any user corrections appended as an
    explicit "Refinements" block the LLM is instructed to honor. We append
    rather than rewrite so the LLM can see both the original extraction and
    the human override side-by-side, and call out the disagreement honestly.
    """
    corrs = list_corrections(ws)
    if not corrs:
        return rendered_subgraph

    # Filter to corrections that target nodes present in the subgraph. We
    # detect membership cheaply via label search — the rendered subgraph
    # already prints labels.
    relevant: list[dict[str, Any]] = []
    for c in corrs:
        if c["kind"] == "addition":
            # Additions always apply (they're new facts).
            relevant.append(c)
            continue
        target = c.get("target_node_id")
        if not target:
            continue
        label = (node_id_to_label or {}).get(target) or target
        if label and label in rendered_subgraph:
            relevant.append(c)

    if not relevant:
        return rendered_subgraph

    lines = [
        "",
        "REFINEMENTS (user-curated layer applied on top of the graph extraction).",
        "Honor these over the graph when there's a conflict; cite the author when",
        "the assertion is human-experience. Surface dissents alongside the original.",
        "",
    ]
    for c in relevant:
        target_label = (node_id_to_label or {}).get(c.get("target_node_id") or "", c.get("target_node_id") or "(new)")
        prefix = {
            "correction": "CORRECTION",
            "addition": "ADDITION",
            "attestation": "ATTESTATION",
            "dissent": "DISSENT",
        }[c["kind"]]
        author_bits = []
        if c.get("author"):
            author_bits.append(c["author"])
        if c.get("author_basis"):
            author_bits.append(c["author_basis"])
        author_str = " — ".join(author_bits) if author_bits else "anonymous"
        line = f"- {prefix} on {target_label!r}: "
        if c["kind"] in ("correction", "addition"):
            if c.get("original_summary"):
                line += f"original said {c['original_summary']!r}; "
            line += f"now: {c['new_summary']!r}"
        elif c["kind"] == "attestation":
            line += f"verified ({c['original_summary'] or 'as stated'})"
        elif c["kind"] == "dissent":
            line += f"challenged ({c.get('new_summary') or c.get('reason') or 'no detail'})"
        extras = []
        extras.append(f"by {author_str}")
        extras.append(f"confidence={c.get('confidence', 'medium')}")
        if c.get("evidence_url"):
            extras.append(f"evidence={c['evidence_url']}")
        if c.get("reason"):
            extras.append(f"reason: {c['reason']}")
        line += " [" + "; ".join(extras) + "]"
        lines.append(line)
    return rendered_subgraph + "\n" + "\n".join(lines)
