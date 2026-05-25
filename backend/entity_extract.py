"""Deterministic, LLM-free entity typing + role-edge extraction.

Runs as a post-pass inside `graphify_runner.rebuild_graph`, after Claude's
semantic extraction is merged into the graph but before clustering. Adds
two things on top of the existing graph:

1. `entity_type` annotations on nodes whose label matches a known entity
   pattern — one of {"person", "company", "organization", "product"}.
   Uses cheap regex/heuristic rules. No LLM calls. No NER model.

2. Typed role edges between matched entities — `works_at`, `founded`,
   `invested_in`, `attended`, `advises`. These complement the semantic
   edges Claude emits (cites/references/etc.) with social-graph
   relations the LLM doesn't reliably surface.

Both passes are intentionally conservative — precision over recall. A
pattern miss yields no annotation (graceful); a false positive looks
wrong in the graph. Users can refine via the Refine KB tab (Fix / Add /
Confirm / Doubt) just like any other extracted fact.

The deterministic pass only operates on plain-text source files
(`.md`, `.txt`, `.rst`, `.html`) and on existing node labels. PDFs and
images are Claude's territory; we don't try to re-extract them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Entity typing — heuristic classifier
# ---------------------------------------------------------------------------

# Suffixes that strongly imply "this is a legal entity". We check whole-word
# matches (with leading space or comma) so we don't false-positive on names
# that happen to contain "Inc" or "Ltd" mid-token.
_COMPANY_SUFFIXES = (
    "Inc.", "Inc", "LLC", "L.L.C.", "Corp.", "Corp", "Corporation",
    "GmbH", "Ltd.", "Ltd", "Co.", "PLC", "AG", "BV", "B.V.", "SA",
    "S.A.", "AB", "Pty", "Pty.", "PBC", "S.p.A.",
)

# Multi-word particle tokens that should NOT disqualify a name from being
# classified as a person (e.g. "Jane van der Linden", "Carlos de la Cruz").
_NAME_PARTICLES = frozenset({
    "de", "von", "van", "der", "den", "le", "la", "du", "el", "al",
    "bin", "ibn", "ben", "y", "san", "santa",
})

# Org/place/product trailing tokens — when a Title-Case phrase ends in one
# of these, classify it as an organization (or company) rather than a
# person. "Beta Holdings", "Acme Robotics", "Carnegie Mellon University".
# Without this, two-token Title-Case phrases collapse to "person" too eagerly.
_ORG_TRAILING_TOKENS = frozenset({
    "Holdings", "Holding", "Robotics", "Systems", "Capital", "Ventures",
    "Partners", "Group", "Industries", "Technologies", "Solutions",
    "Foundation", "Institute", "Labs", "Lab", "Research", "Network",
    "University", "College", "School", "Hospital", "Clinic",
    "Media", "Studios", "Studio", "Games", "Bank", "Trust", "Fund",
    "Insurance", "Health", "Healthcare", "Energy", "Power", "Motors",
    "Auto", "Air", "Airlines", "Rail", "Logistics", "Shipping",
})
_PLACE_TRAILING_TOKENS = frozenset({
    "University", "College", "School", "Institute", "Academy",
})

ENTITY_TYPES = ("person", "company", "organization", "product")


def classify_entity_type(label: str) -> str | None:
    """Return one of {'person', 'company', 'organization', 'product'} or
    None when the label doesn't fit any rule confidently. Heuristic-only.

    Precision-tuned. We'd rather skip a real entity than mislabel one —
    a wrong label is harder to spot than a missing one.
    """
    if not label:
        return None
    s = label.strip().rstrip(".,;:")
    if not s:
        return None

    # Rule 1 — Legal-entity suffix → company. Most reliable signal.
    for suf in _COMPANY_SUFFIXES:
        if s.endswith(f" {suf}") or s.endswith(f", {suf}"):
            return "company"
        # Bare suffix as last token: "Acme Inc"
        if s.split()[-1].rstrip(".,") == suf.rstrip("."):
            return "company"

    # Rule 2 — All-caps short token (2-6 alpha chars) → organization.
    #   Matches: IBM, NASA, EU, FDA. Skips: A, AI.
    if (
        s.isupper()
        and 2 <= len(s) <= 6
        and s.isalpha()
        and " " not in s
    ):
        return "organization"

    # Rule 2.5 — Trailing-token signal beats person. A Title-Case phrase
    # that ends in an org/place token ("Holdings", "Robotics", "University")
    # is an organization, not a person, even with 2 title-case tokens.
    tokens_pre = s.split()
    if tokens_pre:
        last = tokens_pre[-1].rstrip(".,")
        if last in _PLACE_TRAILING_TOKENS:
            return "organization"
        if last in _ORG_TRAILING_TOKENS:
            return "organization"

    # Rule 3 — Person: 2-4 title-case tokens, all alpha (allowing hyphens /
    #   apostrophes / particle words). Avoid common false positives by
    #   requiring at least one token >= 3 chars and rejecting all-uppercase.
    tokens = s.split()
    if 2 <= len(tokens) <= 4:
        def _looks_like_name_token(t: str) -> bool:
            t = t.rstrip(".,'")
            if not t:
                return False
            if t.lower() in _NAME_PARTICLES:
                return True
            if not (t[0].isupper()):
                return False
            rest = t[1:]
            if not rest:
                return False
            # Allow internal hyphens / apostrophes in names (e.g. "O'Connor").
            cleaned = rest.replace("-", "").replace("'", "")
            if not cleaned:
                return False
            # Reject ALL-CAPS rest (rules out "MICROSOFT" being treated as a name).
            return cleaned.islower() or any(ch.islower() for ch in cleaned)

        if (
            all(_looks_like_name_token(t) for t in tokens)
            and any(len(t.rstrip(".,'")) >= 3 for t in tokens)
            and not any(t.rstrip(".,").lower() in {"the", "and", "or", "but"} for t in tokens)
        ):
            return "person"

    # No confident match.
    return None


def annotate_entity_types(node_iter: Iterable[Any]) -> int:
    """Walk a NetworkX node iterator (`G.nodes(data=True)` style) and
    annotate each node with `entity_type` when its label matches a rule.

    Returns the count of newly typed nodes. Existing `entity_type` values
    are preserved (human refinements, prior passes).

    Skips code-derived nodes (`file_type == "code"`) — their labels are
    identifiers like `BADGE` or `useEffect` that the heuristic would
    mistake for organizations or persons. Entity typing is meaningful
    only for prose / document corpora.
    """
    typed = 0
    for nid, data in node_iter:
        if not isinstance(data, dict):
            continue
        if data.get("entity_type"):
            continue
        if (data.get("file_type") or "").lower() == "code":
            continue
        et = classify_entity_type(data.get("label") or nid or "")
        if et:
            data["entity_type"] = et
            typed += 1
    return typed


# ---------------------------------------------------------------------------
# Role-edge extraction — regex pattern matching
# ---------------------------------------------------------------------------

# An "entity phrase" is 1-4 Title-Case tokens, optionally joined by simple
# punctuation. Tight enough to avoid runaway captures; loose enough to catch
# "Acme AI", "Jane Doe", "S&P 500", "Microsoft Research".
_ENTITY_TOKEN = r"[A-Z][A-Za-z0-9'\-]+(?:\.[A-Z][A-Za-z0-9'\-]+)*"
_ENTITY_PHRASE = rf"{_ENTITY_TOKEN}(?:\s+{_ENTITY_TOKEN}){{0,3}}"

# Each entry: (compiled regex, relation, swap_subj_obj?).
# The default capture order is (subject, object); some patterns express the
# relation in reverse ("Y employs X") and need the swap flag.
_ROLE_PATTERNS: list[tuple[re.Pattern, str, bool]] = [
    # X works/worked at|for Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+work(?:s|ed)?\s+(?:at|for)\s+({_ENTITY_PHRASE})\b"), "works_at", False),
    # X joined Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+joined\s+({_ENTITY_PHRASE})\b"), "works_at", False),
    # X is a/the [role] at|of Y  e.g. "Jane is CEO of Acme", "Bob is the CTO at Foo Inc"
    (re.compile(
        rf"\b({_ENTITY_PHRASE})\s+is\s+(?:an?|the)\s+[A-Z]?[A-Za-z]+(?:\s+[A-Z]?[A-Za-z]+){{0,2}}\s+(?:at|of)\s+({_ENTITY_PHRASE})\b"
    ), "works_at", False),

    # X (co-)founded Y / X is a/the (co-)founder of Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+(?:co\-?)?founded\s+({_ENTITY_PHRASE})\b"), "founded", False),
    (re.compile(
        rf"\b({_ENTITY_PHRASE})\s+is\s+(?:an?|the)\s+(?:co\-?)?founder\s+of\s+({_ENTITY_PHRASE})\b"
    ), "founded", False),

    # X invested in Y / X (led|backed|participated in) the [...] round (in|of) Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+invested\s+in\s+({_ENTITY_PHRASE})\b"), "invested_in", False),
    (re.compile(
        rf"\b({_ENTITY_PHRASE})\s+(?:led|backed|participated\s+in)\s+(?:the\s+)?[\w\s\-]{{0,30}}?round\s+(?:in|of)\s+({_ENTITY_PHRASE})\b"
    ), "invested_in", False),

    # X attended Y / X studied at Y / X graduated from Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+attended\s+({_ENTITY_PHRASE})\b"), "attended", False),
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+studied\s+at\s+({_ENTITY_PHRASE})\b"), "attended", False),
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+graduated\s+from\s+({_ENTITY_PHRASE})\b"), "attended", False),

    # X advises|advised Y / X is an? advisor (to|of|for) Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+advise[sd]?\s+({_ENTITY_PHRASE})\b"), "advises", False),
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+is\s+an?\s+advisor\s+(?:to|of|for)\s+({_ENTITY_PHRASE})\b"), "advises", False),
    # X sits on the board of Y / X joined the board of Y
    (re.compile(rf"\b({_ENTITY_PHRASE})\s+(?:sits|joined|joins)\s+(?:on\s+)?the\s+board\s+of\s+({_ENTITY_PHRASE})\b"), "advises", False),
]

ROLE_RELATIONS = frozenset({"works_at", "founded", "invested_in", "attended", "advises"})


# Common false positives — sentence-leading articles/pronouns that get title-
# cased and look like entity phrases.
_FALSE_POSITIVE_HEADS = frozenset({
    "The", "A", "An", "He", "She", "They", "It", "We", "You", "This",
    "That", "These", "Those", "Who", "What", "When", "Where", "Why",
    "Our", "Your", "His", "Her", "Their", "My",
})


def extract_role_edges_from_text(text: str, source_file: str) -> list[dict[str, Any]]:
    """Run regex-based role-edge extraction on a block of text.

    Returns a list of {subject_label, object_label, relation, confidence,
    source_file, extractor} dicts. The caller resolves labels to existing
    graph node ids before adding edges (so we don't create new entities —
    that's a future expansion).
    """
    if not text:
        return []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern, relation, _swap in _ROLE_PATTERNS:
        for m in pattern.finditer(text):
            subj = (m.group(1) or "").strip(" ,;.:'\"")
            obj = (m.group(2) or "").strip(" ,;.:'\"")
            if not subj or not obj or subj == obj:
                continue
            # Drop matches where the head token is a function word that
            # happens to start a sentence (e.g. "The team works at Acme").
            head_subj = subj.split(" ", 1)[0]
            head_obj = obj.split(" ", 1)[0]
            if head_subj in _FALSE_POSITIVE_HEADS or head_obj in _FALSE_POSITIVE_HEADS:
                continue
            key = (subj.lower(), relation, obj.lower())
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "subject_label": subj,
                "object_label": obj,
                "relation": relation,
                "confidence": "DETERMINISTIC",
                "confidence_score": 1.0,
                "source_file": source_file,
                "extractor": "regex_role_pattern",
            })
    return edges


# File suffixes we scan with the regex pass. PDFs and images are Claude's
# territory; binary/vendor formats wouldn't yield clean text anyway.
_SCANNABLE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst", ".html", ".htm"})


def extract_role_edges_from_raw_dir(raw_dir: Path, max_bytes_per_file: int = 1_000_000) -> list[dict[str, Any]]:
    """Walk `raw_dir` recursively, scan every plain-text file under it for
    role-edge patterns, and return the merged edge-spec list. Skips files
    larger than `max_bytes_per_file` to keep the pass bounded on very large
    documents (long markdown changelogs, etc.)."""
    edges: list[dict[str, Any]] = []
    if not raw_dir or not raw_dir.exists():
        return edges
    for fp in raw_dir.rglob("*"):
        if not fp.is_file():
            continue
        if fp.suffix.lower() not in _SCANNABLE_SUFFIXES:
            continue
        try:
            if fp.stat().st_size > max_bytes_per_file:
                continue
            text = fp.read_text(errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        edges.extend(extract_role_edges_from_text(text, fp.name))
    return edges


# ---------------------------------------------------------------------------
# Graph integration — resolve labels to ids + add edges
# ---------------------------------------------------------------------------

def merge_role_edges_into_graph(
    G: Any, edge_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve subject/object labels to existing node ids (case-insensitive
    label match) and add typed role edges to the graph.

    Returns {"added": int, "skipped_unresolved": int} so the caller can
    surface stats in the rebuild meta block.

    We deliberately DO NOT create new nodes — role-edge matches only link
    entities already extracted by Claude / the AST pass. That keeps the
    pass safe (no spurious nodes from regex false-positives) and makes the
    role-edge layer purely additive. Creating new typed nodes is a clean
    follow-on once the precision of the basic pass is validated in real
    use.
    """
    added = 0
    skipped = 0
    if not edge_specs:
        return {"added": 0, "skipped_unresolved": 0}

    # Build a case-insensitive label → node-id index.
    label_to_id: dict[str, str] = {}
    for nid, data in G.nodes(data=True):
        if not isinstance(data, dict):
            continue
        label = (data.get("label") or "").strip().lower()
        if label and label not in label_to_id:
            label_to_id[label] = nid

    for spec in edge_specs:
        subj_id = label_to_id.get(spec["subject_label"].lower())
        obj_id = label_to_id.get(spec["object_label"].lower())
        if not subj_id or not obj_id:
            skipped += 1
            continue
        if G.has_edge(subj_id, obj_id):
            # Don't overwrite a Claude-emitted semantic edge. If we wanted
            # to encode "both" relations on one edge, we'd extend the edge
            # data here. For now, prefer the existing semantic edge.
            continue
        G.add_edge(
            subj_id, obj_id,
            relation=spec["relation"],
            confidence=spec["confidence"],
            confidence_score=spec["confidence_score"],
            source_file=spec["source_file"],
            extractor=spec.get("extractor", "regex_role_pattern"),
            weight=1.0,
        )
        added += 1

    return {"added": added, "skipped_unresolved": skipped}
