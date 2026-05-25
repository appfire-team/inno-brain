"""Thin wrapper around graphify's pipeline so the API stays small."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import networkx as nx
from networkx.readwrite import json_graph

from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.build import build_from_json
from graphify.cluster import cluster as cluster_graph, score_all
from graphify.detect import detect, save_manifest
from graphify.export import to_json
from graphify.extract import collect_files, extract as extract_ast

from workspaces import Workspace

# Rich extraction prompt — mirrors what /graphify's subagents emit. Keeps the
# audit trail (EXTRACTED / INFERRED / AMBIGUOUS) and the schema the rest of the
# pipeline already expects.
EXTRACTION_SYSTEM_PROMPT = (
    "You extract knowledge graph fragments from documents. "
    "Output ONLY valid JSON matching the requested schema. No markdown fences, no preamble."
)

EXTRACTION_USER_TEMPLATE = """\
Extract a knowledge graph fragment from the attached document.

Source file (use exactly this string for every node's source_file): {source_file}

Rules:
- EXTRACTED: relationship explicit in source (citation, "see §3.2", named decision, direct comparison)
- INFERRED: reasonable inference (shared theme, implied dependency, recurring entity)
- AMBIGUOUS: uncertain — flag for review, do not omit

Extract named concepts, entities, theses, recommendations, market segments, products, companies,
decisions, and rationale sections. Rationale nodes (sections explaining WHY) get `rationale_for`
edges to the concept they explain.

Semantic similarity: if two concepts solve the same problem or represent the same idea without
any explicit link, add a `semantically_similar_to` edge marked INFERRED with confidence_score 0.6-0.95.

Hyperedges: if 3+ nodes participate in a shared concept/flow/pattern not captured by pairwise
edges, add to a top-level `hyperedges` array. Max 3 hyperedges.

confidence_score is REQUIRED on every edge:
- EXTRACTED: 1.0
- INFERRED with direct structural evidence: 0.8-0.9
- INFERRED with some uncertainty: 0.6-0.7
- AMBIGUOUS: 0.1-0.3
Never default to 0.5.

For each node:
- id: snake_case, prefixed with a sluggified version of source_file (e.g. `myfile_some_entity`)
- label: human-readable name (max ~120 chars)
- file_type: "{file_type}"
- source_file: the literal string above
- source_location: page or section if known, else null
- author/contributor/source_url/captured_at: null unless found in the document

Aim for 8-20 nodes and 10-30 edges from a single document. More for long/dense documents.

Output exactly this JSON shape (no other text):

{{
  "nodes": [
    {{"id":"...","label":"...","file_type":"{file_type}","source_file":"{source_file}",
      "source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}}
  ],
  "edges": [
    {{"source":"node_id","target":"node_id",
      "relation":"references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for|implements|calls",
      "confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,
      "source_file":"{source_file}","source_location":null,"weight":1.0}}
  ],
  "hyperedges": [
    {{"id":"snake_case_id","label":"...","nodes":["id1","id2","id3"],
      "relation":"participate_in|implement|form","confidence":"INFERRED",
      "confidence_score":0.8,"source_file":"{source_file}"}}
  ]
}}
"""


def _strip_json(text: str) -> str:
    """Strip code fences and any leading/trailing prose around a JSON object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _parse_extraction_json(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON output, recovering from truncation when possible.

    The LLM occasionally hits max_tokens mid-array. We recover by trimming the
    output back to the last complete `]` or `}` and closing any open structure.
    """
    stripped = _strip_json(raw)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Recovery: walk backwards and look for the last well-formed array close,
    # then close out the surrounding object.
    truncated = stripped
    for closer in ("]", "}"):
        idx = truncated.rfind(closer)
        if idx > 0:
            candidate = truncated[: idx + 1]
            # Balance braces by greedily closing what's still open.
            opens = candidate.count("{") - candidate.count("}")
            opens_brackets = candidate.count("[") - candidate.count("]")
            patched = candidate + ("]" * max(opens_brackets, 0)) + ("}" * max(opens, 0))
            try:
                return json.loads(patched)
            except json.JSONDecodeError:
                continue
    return {}


def _extract_one(client: Any, file_path: Path, file_type: str, model: str) -> dict[str, Any]:
    """Call Claude on a single file and return its node/edge fragment."""
    source_file = file_path.name
    user_text = EXTRACTION_USER_TEMPLATE.format(source_file=source_file, file_type=file_type)

    content: list[dict[str, Any]] = []
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        b64 = base64.b64encode(file_path.read_bytes()).decode()
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        })
        content.append({"type": "text", "text": user_text})
    elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        b64 = base64.b64encode(file_path.read_bytes()).decode()
        media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "gif": "image/gif", "webp": "image/webp"}[suffix[1:]]
        content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
        content.append({"type": "text", "text": user_text})
    else:
        # Plain text: markdown, txt, code, etc.
        try:
            file_text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            file_text = file_path.read_bytes().decode("utf-8", errors="replace")
        # Cap at ~80k chars so we don't blow input limits on huge files.
        if len(file_text) > 80000:
            file_text = file_text[:80000] + "\n... [truncated]"
        content.append({"type": "text", "text": f"<document>\n{file_text}\n</document>\n\n{user_text}"})

    max_tokens = int(os.environ.get("GRAPHIFY_MAX_OUTPUT_TOKENS", "16000"))
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    text_parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts)

    usage = getattr(msg, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
    out_tok = getattr(usage, "output_tokens", 0) if usage else 0

    data = _parse_extraction_json(raw)
    if not data:
        return {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "error": f"Could not parse JSON from {source_file} (output may have been truncated).",
        }

    return {
        "nodes": data.get("nodes", []),
        "edges": data.get("edges", []),
        "hyperedges": data.get("hyperedges", []),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


# Bump when EXTRACTION_SYSTEM_PROMPT / EXTRACTION_USER_TEMPLATE changes so
# the cache invalidates correctly. Keep short — it's part of every cache key.
_SEMANTIC_PROMPT_VERSION = "v1"


def _semantic_cache_key(content_bytes: bytes, model: str, file_type: str) -> str:
    """sha256 of (prompt_version + model + file_type + raw bytes). Any change
    in any axis → new key → cache miss → fresh extraction."""
    import hashlib
    h = hashlib.sha256()
    h.update(_SEMANTIC_PROMPT_VERSION.encode())
    h.update(b"\0")
    h.update(model.encode())
    h.update(b"\0")
    h.update(file_type.encode())
    h.update(b"\0")
    h.update(content_bytes)
    return h.hexdigest()


def _semantic_cache_path(ws: Any, key: str) -> Path:
    # Two-level fanout (first 2 hex chars as subdir) so a workspace with tens
    # of thousands of cached files doesn't blow up a single directory.
    return ws.out_dir / "sem-cache" / key[:2] / f"{key}.json"


def _semantic_cache_load(ws: Any, key: str) -> dict[str, Any] | None:
    p = _semantic_cache_path(ws, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _semantic_cache_save(ws: Any, key: str, value: dict[str, Any]) -> None:
    p = _semantic_cache_path(ws, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2))


def rich_semantic_extract(
    files: list[Path], file_types: list[str], model: str,
    *, ws: "Workspace | None" = None,
) -> dict[str, Any]:
    """Run rich Claude extraction in parallel across multiple files.

    Per-file content-hash cache: before calling Claude on a file we look up
    its sha256 (combined with model + file type + prompt version) in
    `<ws>/graphify-out/sem-cache/`. Hits skip the LLM call entirely and reuse
    the prior nodes/edges. A doc removal that triggers a workspace rebuild
    now finishes in seconds instead of minutes because every surviving file
    is a cache hit.

    If `ws` is given, also reports per-file progress to index_jobs so the UI
    banner shows e.g. '17/294 documents (212 cached)'.
    """
    client = _anthropic_client()
    if not client:
        return {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0,
                "error": "No Anthropic auth available (set ANTHROPIC_API_KEY or log in via `claude` CLI)."}

    max_concurrency = int(os.environ.get("GRAPHIFY_CONCURRENCY", "3"))
    out_nodes: list[dict] = []
    out_edges: list[dict] = []
    out_hyperedges: list[dict] = []
    total_in = 0
    total_out = 0
    errors: list[str] = []
    total = len(files)
    done = 0
    cached_count = 0

    def _report() -> None:
        if ws is not None:
            try:
                import index_jobs as _ij
                _ij.set_message(
                    ws.id,
                    f"LLM extraction: {done}/{total} documents ({cached_count} cached)",
                )
            except Exception:
                pass

    # --- Phase 1: cache lookup -------------------------------------------------
    # Read every file once. On hit, accumulate cached output and skip the LLM
    # call entirely. On miss, queue (path, type, key) for phase 2.
    to_extract: list[tuple[Path, str, str]] = []
    for f, t in zip(files, file_types):
        cache_key: str | None = None
        if ws is not None:
            try:
                content_bytes = f.read_bytes()
                cache_key = _semantic_cache_key(content_bytes, model, t)
                hit = _semantic_cache_load(ws, cache_key)
            except OSError:
                hit = None  # unreadable — let _extract_one surface the error
            if hit is not None:
                out_nodes.extend(hit.get("nodes", []))
                out_edges.extend(hit.get("edges", []))
                out_hyperedges.extend(hit.get("hyperedges", []))
                # No tokens charged for a cache hit.
                done += 1
                cached_count += 1
                _report()
                continue
        to_extract.append((f, t, cache_key or ""))

    # --- Phase 2: extract the misses in parallel -------------------------------
    with ThreadPoolExecutor(max_workers=max_concurrency) as ex:
        futures = {
            ex.submit(_extract_one, client, f, t, model): (f, key)
            for f, t, key in to_extract
        }
        for fut in as_completed(futures):
            f, key = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f.name}: {exc}")
                done += 1
                _report()
                continue
            out_nodes.extend(result.get("nodes", []))
            out_edges.extend(result.get("edges", []))
            out_hyperedges.extend(result.get("hyperedges", []))
            total_in += result.get("input_tokens", 0)
            total_out += result.get("output_tokens", 0)
            if "error" in result:
                errors.append(result["error"])
            elif ws is not None and key:
                # Cache the successful extraction. We exclude tokens from
                # the cached payload because they reflect the ORIGINAL call;
                # future hits report tokens=0 (no spend).
                _semantic_cache_save(ws, key, {
                    "nodes": result.get("nodes", []),
                    "edges": result.get("edges", []),
                    "hyperedges": result.get("hyperedges", []),
                    "source_file": f.name,
                    "cached_at": time.time(),
                })
            done += 1
            _report()

    # De-dup nodes by id (keep first occurrence).
    seen: set[str] = set()
    dedup_nodes = []
    for n in out_nodes:
        nid = n.get("id")
        if nid and nid not in seen:
            seen.add(nid)
            dedup_nodes.append(n)

    return {
        "nodes": dedup_nodes,
        "edges": out_edges,
        "hyperedges": out_hyperedges,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "errors": errors,
    }


def _safe_filename(name: str) -> str:
    """Strip path components — only keep the basename, no slashes."""
    return Path(name).name.replace("..", "_")


def save_upload(ws: Workspace, filename: str, data: bytes) -> Path:
    """Write an uploaded file to the workspace's raw/ and return its path."""
    ws.ensure_dirs()
    target = ws.raw_dir / _safe_filename(filename)
    target.write_bytes(data)
    return target


def list_uploaded_files(ws: Workspace) -> list[dict[str, Any]]:
    files = []
    if not ws.raw_dir.exists():
        return files
    for p in sorted(ws.raw_dir.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            stat = p.stat()
            files.append({
                "name": p.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "kind": _classify_source(p),
            })
    return files


# File-suffixes we'll cheaply peek into for the URL-ingest YAML header. PDFs,
# code, and binary blobs are always "upload" — never URL/research.
_URL_PEEKABLE_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt"}


def _classify_source(p: Path) -> str:
    """Best-effort categorise a raw file as 'research' | 'url' | 'upload'.

    Cheap signals only:
      - Filename prefix `web-` → research (set by /api/research; see main.py).
      - YAML front-matter `source_url:` in the first 300 bytes of .md/.html
        files → url (graphify.ingest writes this header for every URL fetch).
      - Everything else → upload (PDFs, code, drag-and-drop files).
    """
    name = p.name
    if name.startswith("web-"):
        return "research"
    if p.suffix.lower() in _URL_PEEKABLE_SUFFIXES:
        try:
            head = p.read_bytes()[:300].decode("utf-8", errors="ignore")
        except OSError:
            return "upload"
        if head.lstrip().startswith("---") and "source_url:" in head:
            return "url"
    return "upload"


def source_kind_counts(ws: Workspace) -> dict[str, int]:
    """Aggregate `list_uploaded_files` by `kind` for the header pills.

    Repos live as subdirs (see `list_repos`) and are counted separately by
    callers — this only counts loose files in `raw/`.
    """
    counts = {"upload": 0, "url": 0, "research": 0}
    for f in list_uploaded_files(ws):
        counts[f.get("kind", "upload")] = counts.get(f.get("kind", "upload"), 0) + 1
    return counts


def graph_exists(ws: Workspace) -> bool:
    return ws.graph_file.exists()


def load_graph(ws: Workspace) -> nx.Graph:
    if not graph_exists(ws):
        return nx.Graph()
    data = json.loads(ws.graph_file.read_text())
    return json_graph.node_link_graph(data, edges="links")


def graph_stats(ws: Workspace) -> dict[str, Any]:
    file_kinds = source_kind_counts(ws)
    files_total = sum(file_kinds.values())
    repos_total = len(list_repos(ws))
    sources = {
        "docs": file_kinds.get("upload", 0),
        "urls": file_kinds.get("url", 0),
        "research": file_kinds.get("research", 0),
        "repos": repos_total,
    }
    if not graph_exists(ws):
        return {
            "nodes": 0,
            "edges": 0,
            "communities": 0,
            "files": files_total,
            "sources": sources,
            "has_graph": False,
        }
    G = load_graph(ws)
    comms: set[int] = set()
    for _, d in G.nodes(data=True):
        c = d.get("community")
        if c is not None:
            comms.add(c)
    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "communities": len(comms),
        "files": files_total,
        "sources": sources,
        "has_graph": True,
    }


def rebuild_graph(ws: Workspace) -> dict[str, Any]:
    """Full pipeline: detect → extract (AST + LLM) → build → cluster → save graph.json."""
    with ws.pipeline_lock:
        return _rebuild_inner(ws)


def _progress(ws: Workspace, message: str) -> None:
    """Best-effort progress hook → index_jobs banner. Safe no-op when no job
    is active (e.g. when rebuild_graph is called synchronously from tests)."""
    try:
        import index_jobs as _ij
        _ij.set_message(ws.id, message)
    except Exception:
        pass


def _rebuild_inner(ws: Workspace) -> dict[str, Any]:
    # If the workspace dir was deleted between the request hitting active_workspace
    # and reaching here (e.g. user clicked "Delete workspace" mid-flight), bail
    # gracefully instead of 500ing in to_json with a confusing FileNotFoundError.
    if not ws.path.exists():
        return {
            "nodes": 0,
            "edges": 0,
            "communities": 0,
            "files": 0,
            "message": "Workspace was deleted before rebuild completed.",
        }
    ws.ensure_dirs()
    _progress(ws, "Scanning files…")
    detection = detect(ws.raw_dir)
    if detection["total_files"] == 0:
        if ws.graph_file.exists():
            ws.graph_file.unlink()
        return {
            "nodes": 0,
            "edges": 0,
            "communities": 0,
            "files": 0,
            "message": "No files uploaded yet.",
        }

    # --- AST extraction (deterministic, free) -----------------------------------
    ast_paths: list[Path] = []
    for f in detection["files"].get("code", []):
        p = Path(f)
        ast_paths.extend(collect_files(p) if p.is_dir() else [p])
    if ast_paths:
        _progress(ws, f"AST extraction over {len(ast_paths)} code files…")
    ast_result = extract_ast(ast_paths) if ast_paths else {"nodes": [], "edges": []}

    # --- Semantic extraction (LLM-backed, only for non-code files) --------------
    # We bypass graphify.llm because v0.8.2's prompt only emits a single
    # document-level node. Our rich_semantic_extract uses the same prompt the
    # /graphify CLI skill uses, which produces real entity/relationship graphs.
    non_code: list[Path] = []
    non_code_types: list[str] = []
    for kind in ("document", "paper", "image"):
        for f in detection["files"].get(kind, []):
            non_code.append(Path(f))
            non_code_types.append(kind)

    sem_nodes: list[dict] = []
    sem_edges: list[dict] = []
    sem_hyperedges: list[dict] = []
    sem_meta: dict[str, Any] = {"backend": None, "extracted_files": 0}

    # LLM semantic extraction is one Claude call per file. For document workspaces
    # (a handful of PDFs) this is fine. For a code repo containing hundreds of
    # markdown files it explodes to 20-30 minutes wall clock and the client
    # disconnects long before the graph is saved. Cap it.
    sem_max_files = int(os.environ.get("GRAPHIFY_SEMANTIC_MAX_FILES", "500"))
    if non_code and len(non_code) > sem_max_files:
        sem_meta["error"] = (
            f"Skipped LLM extraction: {len(non_code)} non-code files exceeds limit "
            f"({sem_max_files}). The AST graph still builds. Raise "
            f"GRAPHIFY_SEMANTIC_MAX_FILES to enrich docs at the cost of wall-clock time."
        )
        sem_meta["semantic_files_skipped"] = len(non_code)
        non_code = []
        non_code_types = []

    if non_code:
        # rich_semantic_extract resolves its own client (env var → keychain
        # fallback). Trust its return shape: if `error` is set, no client was
        # available; otherwise extraction ran (possibly with per-file errors
        # in `errors`).
        model = os.environ.get("GRAPHIFY_MODEL", "claude-sonnet-4-6")
        _progress(ws, f"LLM extraction over {len(non_code)} document files (this is the slow step)…")
        result = rich_semantic_extract(non_code, non_code_types, model=model, ws=ws)
        if result.get("error"):
            sem_meta["error"] = result["error"]
        else:
            sem_nodes = result.get("nodes", [])
            sem_edges = result.get("edges", [])
            sem_hyperedges = result.get("hyperedges", [])
            sem_meta["backend"] = "claude"
            sem_meta["model"] = model
            sem_meta["extracted_files"] = len(non_code)
            sem_meta["input_tokens"] = result.get("input_tokens", 0)
            sem_meta["output_tokens"] = result.get("output_tokens", 0)
            if result.get("errors"):
                sem_meta["errors"] = result["errors"]

    # --- Merge AST + semantic ---------------------------------------------------
    seen_ids = {n["id"] for n in ast_result["nodes"]}
    merged_nodes = list(ast_result["nodes"])
    for n in sem_nodes:
        if n["id"] not in seen_ids:
            merged_nodes.append(n)
            seen_ids.add(n["id"])

    extraction = {
        "nodes": merged_nodes,
        "edges": ast_result["edges"] + sem_edges,
        "hyperedges": sem_hyperedges,
    }

    # --- Build graph, cluster, save --------------------------------------------
    _progress(ws, "Building graph + clustering…")
    G = build_from_json(extraction)
    if G.number_of_nodes() == 0:
        return {
            "nodes": 0,
            "edges": 0,
            "communities": 0,
            "files": detection["total_files"],
            "message": "No graph nodes were produced — likely AST-only with no code files, or LLM extraction failed.",
            "meta": sem_meta,
        }

    # Cross-document linking sends every (id, label) tuple to Claude. For dense
    # code graphs this exceeds the context window and either errors out or stalls
    # the request long enough that the client disconnects. Skip it past a node
    # threshold — the user can still trigger it manually via "Link docs" after
    # the rebuild lands, by which point they've decided the cost is worth it.
    link_node_limit = int(os.environ.get("GRAPHIFY_AUTOLINK_MAX_NODES", "2000"))
    link_meta: dict[str, Any] = {"edges_added": 0}
    if os.environ.get("GRAPHIFY_AUTOLINK", "1") == "0":
        link_meta["skipped"] = "GRAPHIFY_AUTOLINK=0"
    elif G.number_of_nodes() > link_node_limit:
        link_meta["skipped"] = (
            f"graph too large ({G.number_of_nodes()} nodes > {link_node_limit}); "
            f"use 'Link docs' to run cross-doc linking on demand"
        )
    else:
        link_result = _cross_document_link(G)
        link_meta = {
            "edges_added": link_result["edges_added"],
            "input_tokens": link_result["meta"].get("input_tokens", 0),
            "output_tokens": link_result["meta"].get("output_tokens", 0),
        }
    sem_meta["cross_link"] = link_meta

    # Deterministic entity-typing + role-edge pass. Zero LLM calls. Runs
    # after Claude extraction so it can both annotate existing nodes and
    # link them with social-graph edges (works_at / founded / invested_in
    # / attended / advises). Conservative: precision over recall.
    try:
        import entity_extract as _ee
        typed_count = _ee.annotate_entity_types(list(G.nodes(data=True)))
        role_specs = _ee.extract_role_edges_from_raw_dir(ws.raw_dir)
        role_merge = _ee.merge_role_edges_into_graph(G, role_specs)
        sem_meta["entity_pass"] = {
            "nodes_typed": typed_count,
            "role_edges_added": role_merge["added"],
            "role_edges_unresolved": role_merge["skipped_unresolved"],
            "role_specs_total": len(role_specs),
        }
    except Exception as exc:  # noqa: BLE001 — entity pass must never break rebuild
        sem_meta["entity_pass"] = {"error": str(exc)}

    communities = cluster_graph(G)
    cohesion = score_all(G, communities)

    # Auto-labelling sends every community's sample members to Claude in one
    # prompt. With hundreds of communities the prompt blows past the context
    # window. Past a threshold, fall back to numeric "Community N" labels.
    label_limit = int(os.environ.get("GRAPHIFY_AUTOLABEL_MAX_COMMUNITIES", "120"))
    if len(communities) > label_limit:
        community_labels = {cid: f"Community {cid}" for cid in communities}
        sem_meta["auto_label"] = {
            "skipped": f"too many communities ({len(communities)} > {label_limit})"
        }
    else:
        community_labels = _auto_label_communities(G, communities)
    # Write community labels onto each node so the viz / graph endpoint sees them.
    for cid, members in communities.items():
        label = community_labels.get(cid, f"Community {cid}")
        for nid in members:
            if nid in G.nodes:
                G.nodes[nid]["community_label"] = label

    # Defensive: re-ensure the output dir right before write. Catches the race
    # where another request deletes the workspace mid-rebuild (e.g. user clicks
    # "Delete workspace" while a `delete_repo` rebuild is still running).
    ws.graph_file.parent.mkdir(parents=True, exist_ok=True)
    _progress(ws, "Saving graph + computing insights…")
    to_json(G, communities, str(ws.graph_file))
    _stamp_graph_provenance(ws, kind="full")

    insights = {
        "communities": {str(k): v for k, v in communities.items()},
        "community_labels": {str(k): v for k, v in community_labels.items()},
        "cohesion": {str(k): v for k, v in cohesion.items()},
        "gods": god_nodes(G),
        "surprises": surprising_connections(G, communities),
        "questions": suggest_questions(G, communities, community_labels),
    }
    ws.insights_file.write_text(json.dumps(insights, indent=2))

    save_manifest(detection["files"])

    # Refresh the semantic-vector index alongside the graph. Graceful no-op when
    # no embedding provider is configured — the existing degree-sort + LLM
    # router path stays the fallback.
    _progress(ws, "Refreshing semantic embeddings index…")
    try:
        import embeddings as _emb  # local import to keep startup decoupled
        emb_stats = _emb.update_index(ws, G)
    except Exception as exc:  # noqa: BLE001 — must never break ingest
        emb_stats = {"skipped": True, "error": str(exc)}

    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "communities": len(communities),
        "files": detection["total_files"],
        "meta": sem_meta,
        "embeddings": emb_stats,
    }


def _stamp_graph_provenance(ws: Workspace, *, kind: str) -> None:
    """Post-process graph.json to add `extracted_at` to every node and a
    top-level `built_at` timestamp. Also diffs against the prior version (if
    one was just overwritten) and persists `data/workspaces/{ws}/kb_diff.json`
    so the Refine KB tab can surface what changed.

    Pure metadata — we don't touch labels/edges/relations, only annotate.
    """
    import time as _t
    if not ws.graph_file.exists():
        return
    try:
        data = json.loads(ws.graph_file.read_text())
    except Exception:
        return
    now = _t.time()
    # Diff vs. the prior snapshot, if we have one stored.
    snapshot_path = ws.path / "kb_snapshot_prev.json"
    diff: dict[str, Any] | None = None
    if snapshot_path.exists():
        try:
            prev = json.loads(snapshot_path.read_text())
            diff = _diff_graphs(prev, data)
            diff["computed_at"] = now
            diff["kind"] = kind
            (ws.path / "kb_diff.json").write_text(json.dumps(diff, indent=2))
        except Exception:
            pass
    # Stamp extraction times. Nodes that existed before keep their original
    # extracted_at (carried forward via the prior snapshot); new nodes get `now`.
    prior_ids: set[str] = set()
    prior_node_times: dict[str, float] = {}
    if snapshot_path.exists():
        try:
            prev = json.loads(snapshot_path.read_text())
            for n in prev.get("nodes", []):
                prior_ids.add(n.get("id"))
                if n.get("extracted_at") is not None:
                    prior_node_times[n["id"]] = n["extracted_at"]
        except Exception:
            pass
    for n in data.get("nodes", []):
        nid = n.get("id")
        if nid in prior_node_times:
            n["extracted_at"] = prior_node_times[nid]
        elif "extracted_at" not in n:
            n["extracted_at"] = now
    data["built_at"] = now
    ws.graph_file.write_text(json.dumps(data, indent=2))
    # Snapshot the new graph so the next rebuild can diff against it.
    snapshot_path.write_text(json.dumps({
        "nodes": [{"id": n.get("id"), "label": n.get("label"), "source_file": n.get("source_file"),
                   "extracted_at": n.get("extracted_at")} for n in data.get("nodes", [])],
        "edges": [
            {"source": e.get("source"), "target": e.get("target"), "relation": e.get("relation")}
            for e in (data.get("links") or data.get("edges") or [])
        ],
        "built_at": now,
    }, indent=2))


def _diff_graphs(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, Any]:
    """Compute added / removed / relabeled node sets between two graph payloads.
    Edges aren't diffed in detail — just counts — because the curation surface
    cares mainly about which entities entered/left and which got renamed.
    """
    prev_nodes = {n.get("id"): n for n in (prev.get("nodes") or [])}
    curr_nodes = {n.get("id"): n for n in (curr.get("nodes") or [])}
    prev_ids = set(prev_nodes.keys())
    curr_ids = set(curr_nodes.keys())
    added_ids = sorted(curr_ids - prev_ids)
    removed_ids = sorted(prev_ids - curr_ids)
    relabeled: list[dict[str, Any]] = []
    for nid in (prev_ids & curr_ids):
        old_label = (prev_nodes[nid] or {}).get("label")
        new_label = (curr_nodes[nid] or {}).get("label")
        if old_label and new_label and old_label != new_label:
            relabeled.append({"id": nid, "old": old_label, "new": new_label})
    prev_edges = len(prev.get("edges") or prev.get("links") or [])
    curr_edges = len(curr.get("links") or curr.get("edges") or [])
    return {
        "added": [{"id": i, "label": curr_nodes[i].get("label"),
                   "source_file": curr_nodes[i].get("source_file")} for i in added_ids],
        "removed": [{"id": i, "label": prev_nodes[i].get("label"),
                     "source_file": prev_nodes[i].get("source_file")} for i in removed_ids],
        "relabeled": relabeled,
        "counts": {
            "nodes_before": len(prev_ids),
            "nodes_after": len(curr_ids),
            "edges_before": prev_edges,
            "edges_after": curr_edges,
        },
    }


def delete_file(ws: Workspace, filename: str) -> bool:
    target = ws.raw_dir / _safe_filename(filename)
    if not target.exists() or not target.is_file():
        return False
    target.unlink()
    return True


def get_insights(ws: Workspace) -> dict[str, Any]:
    if not ws.insights_file.exists():
        return {"gods": [], "surprises": [], "questions": []}
    return json.loads(ws.insights_file.read_text())


_CLAUDE_OAUTH_CACHE: dict[str, Any] = {"checked": False, "token": None}


def humanize_anthropic_error(exc: BaseException) -> str:
    """Turn the SDK's terse error into something the user can act on.

    Specifically catches 429s (rate limit) and 401s (auth failure) since those
    are the two failure modes that genuinely change what the user should do.
    """
    msg = str(exc)
    on_subscription = (
        not os.environ.get("ANTHROPIC_API_KEY")
        and _CLAUDE_OAUTH_CACHE.get("token")
    )
    if "429" in msg or "rate_limit" in msg:
        if on_subscription:
            return (
                "Rate limit hit on your Claude Code subscription. "
                "Subscription auth has tighter per-minute caps than an API key. "
                "Wait a minute and retry, or set ANTHROPIC_API_KEY in .env for "
                "higher throughput."
            )
        return "Rate limit hit. Wait a minute and retry, or check your account quota."
    if "401" in msg or "authentication" in msg.lower():
        if on_subscription:
            return (
                "Subscription auth rejected (token may have expired). "
                "Re-login with `claude` CLI and restart the backend."
            )
        return "Anthropic auth failed — check ANTHROPIC_API_KEY."
    return msg


def _claude_code_oauth_token() -> str | None:
    """Best-effort lookup of the local Claude Code subscription's OAuth access
    token. Used as a fallback when ANTHROPIC_API_KEY isn't set, so the backend
    can run on the user's Claude Code Pro/Max plan instead of a separate API
    key.

    Resolution order:
      1. Flat JSON at ~/.claude/.credentials.json (Linux / WSL / Docker layout)
      2. macOS keychain entry under service "Claude Code-credentials"

    Caches the first successful read for the process lifetime — we never shell
    out to `security` per request. The token will eventually expire (OAuth
    refresh is handled externally by the Claude CLI); when that happens, the
    SDK call fails with 401 and the operator restarts the backend. Acceptable
    for local dev; not for headless production.
    """
    if _CLAUDE_OAUTH_CACHE["checked"]:
        return _CLAUDE_OAUTH_CACHE["token"]
    _CLAUDE_OAUTH_CACHE["checked"] = True

    import json as _json
    import pathlib as _pl
    import subprocess as _sp
    import sys as _sys

    # 1) Flat JSON file (Linux / WSL / Docker)
    cred_path = _pl.Path("~/.claude/.credentials.json").expanduser()
    if cred_path.exists():
        try:
            d = _json.loads(cred_path.read_text())
            tok = (
                (d.get("claudeAiOauth") or {}).get("accessToken")
                or d.get("access_token")
                or d.get("accessToken")
            )
            if tok:
                _CLAUDE_OAUTH_CACHE["token"] = tok
                print("[anthropic-auth] using Claude Code OAuth token from ~/.claude/.credentials.json", flush=True)
                return tok
        except Exception as exc:
            print(f"[anthropic-auth] credentials.json present but unreadable: {exc}", flush=True)

    # 2) macOS keychain — Claude Code stores the same JSON blob there
    if _sys.platform == "darwin":
        try:
            out = _sp.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10,
            )
            raw = out.stdout.strip()
            if raw:
                try:
                    d = _json.loads(raw)
                    tok = (
                        (d.get("claudeAiOauth") or {}).get("accessToken")
                        or d.get("access_token")
                        or d.get("accessToken")
                    )
                except _json.JSONDecodeError:
                    tok = raw  # already a bare token
                if tok:
                    _CLAUDE_OAUTH_CACHE["token"] = tok
                    print("[anthropic-auth] using Claude Code OAuth token from macOS keychain", flush=True)
                    return tok
        except Exception as exc:
            print(f"[anthropic-auth] keychain read failed: {exc}", flush=True)

    return None


def _anthropic_client() -> Any | None:
    """Return an Anthropic SDK client, or None when no auth is available.

    Resolution order:
      1. ANTHROPIC_API_KEY env var → direct API access (billed against the key).
      2. Claude Code OAuth token (subscription auth) → Bearer + beta header.

    The Bearer fallback piggybacks on the user's Pro/Max plan rather than
    requiring a separate API key. It works because the Messages API accepts
    Authorization: Bearer <oauth> when the anthropic-beta: oauth-2025-04-20
    header is also set — the same path Claude Code itself uses internally.

    `max_retries=6` (SDK default is 2) and a generous timeout reflect that
    subscription auth has tighter per-minute rate limits than API keys —
    bursty synthesis calls deserve a few automatic backoff attempts before
    surfacing a 429 to the user.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    # With a proper API key the rate limits are loose enough that aggressive
    # retries just block the FastAPI worker pool. Keep it modest; bump via env
    # only on subscription auth where 429s are more frequent.
    common = {
        "max_retries": int(os.environ.get("ANTHROPIC_MAX_RETRIES", "3")),
        "timeout": float(os.environ.get("ANTHROPIC_TIMEOUT_S", "90")),
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return Anthropic(api_key=api_key, **common)

    oauth = _claude_code_oauth_token()
    if oauth:
        try:
            return Anthropic(
                auth_token=oauth,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
                **common,
            )
        except TypeError:
            # Very old anthropic SDKs don't expose auth_token; route the
            # Bearer header in manually.
            return Anthropic(
                api_key="placeholder",  # SDK requires *something* non-empty
                default_headers={
                    "Authorization": f"Bearer {oauth}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
                **common,
            )
    return None


def _auto_label_communities(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, str]:
    """Ask Claude for a 2-5 word name for each community based on its node labels.

    Falls back to "Community N" if no API key or the call fails.
    """
    client = _anthropic_client()
    if not client or not communities:
        return {cid: f"Community {cid}" for cid in communities}

    # Build the prompt — small communities omitted to save tokens.
    blocks = []
    for cid, members in communities.items():
        if len(members) < 2:
            continue
        sample = [G.nodes[nid].get("label", nid) for nid in members[:14]]
        blocks.append(f"Community {cid} ({len(members)} nodes):\n" + "\n".join(f"  - {s}" for s in sample))
    if not blocks:
        return {cid: f"Community {cid}" for cid in communities}

    prompt = (
        "Each block below is a community of related nodes from a knowledge graph. "
        "Give each community a concise 2-5 word title (Title Case) that captures the dominant theme.\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn ONLY a JSON object mapping community number (as string) to title. "
        + 'Example: {"0": "Risk Modeling", "1": "User Authentication"}'
    )

    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_LABEL_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        data = json.loads(_strip_json(text))
        labels = {int(k): str(v) for k, v in data.items() if str(v).strip()}
    except Exception:
        labels = {}

    # Backfill anything Claude skipped (tiny communities, parse errors).
    for cid in communities:
        labels.setdefault(cid, f"Community {cid}")
    return labels


def explain_node(
    ws: Workspace,
    query: str,
    *,
    web_grounding: bool = False,
    rubric_body: str = "",
    memory_block: str = "",
) -> dict[str, Any]:
    """Find the best-matching node, return its neighborhood + a synthesized explanation.

    `rubric_body` + `memory_block` are folded into the system prompt so the
    one-off explanation honors the same framing as Conversations/Playbooks."""
    if not graph_exists(ws):
        return {"error": "No graph yet."}
    G = load_graph(ws)
    terms = _query_terms(query)
    if not terms:
        return {"error": "Provide a node name to explain."}

    best_score = 0
    best_id: str | None = None
    for nid, ndata in G.nodes(data=True):
        label = (ndata.get("label") or "").lower()
        score = sum(1 for t in terms if t in label)
        if score > best_score:
            best_score = score
            best_id = nid
    if not best_id:
        return {"error": f"No node matched: {terms}"}

    node = dict(G.nodes[best_id])
    node["id"] = best_id
    node["degree"] = G.degree(best_id)

    neighbors = []
    for n in G.neighbors(best_id):
        ed = G.edges[best_id, n]
        neighbors.append({
            "id": n,
            "label": G.nodes[n].get("label", n),
            "source_file": G.nodes[n].get("source_file"),
            "community_label": G.nodes[n].get("community_label"),
            "relation": ed.get("relation"),
            "confidence": ed.get("confidence"),
            "confidence_score": ed.get("confidence_score"),
        })

    # LLM-synthesized 3-5 sentence explanation, grounded in the neighborhood.
    explanation = None
    web_sources: list[dict[str, str]] = []
    client = _anthropic_client()
    if client:
        rendered = f"NODE: {node.get('label', best_id)}\n  source: {node.get('source_file', '?')}\n"
        for nb in neighbors:
            rendered += f"  --{nb['relation']} [{nb['confidence']}]--> {nb['label']}\n"
        system = (
            "You explain a single knowledge-graph node in 3-5 sentences using its "
            "neighborhood. Cite source_file names when quoting corpus facts."
        )
        if rubric_body:
            system += "\n\n=== RUBRIC (apply these framing rules) ===\n" + rubric_body
        if memory_block:
            system += "\n\n=== PERSISTENT MEMORY ===\n" + memory_block
        if web_grounding:
            system += (
                " You have a web_search tool — use it to add CURRENT real-world context "
                "for this entity (recent news, status, dates) when the corpus is silent. "
                "Cite web sources as 'web: domain.com'."
            )
        kwargs: dict[str, Any] = {
            "model": os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 700 if web_grounding else 500,
            "system": system,
            "messages": [{"role": "user", "content": rendered}],
        }
        if web_grounding:
            kwargs["tools"] = [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "3")),
            }]
        try:
            msg = client.messages.create(**kwargs)
            text_parts = []
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
            explanation = "\n".join(p for p in text_parts if p).strip()
        except Exception as exc:
            explanation = f"(synthesis failed: {exc})"

    return {"node": node, "neighbors": neighbors, "explanation": explanation, "web_sources": web_sources}


def _find_node(G: nx.Graph, query: str) -> str | None:
    terms = _query_terms(query)
    if not terms:
        return None
    best_score = 0
    best_id: str | None = None
    for nid, ndata in G.nodes(data=True):
        label = (ndata.get("label") or "").lower()
        score = sum(1 for t in terms if t in label)
        if score > best_score:
            best_score = score
            best_id = nid
    return best_id


def find_path(ws: Workspace, source: str, target: str) -> dict[str, Any]:
    """Shortest path between two named concepts, with edge details for each hop."""
    if not graph_exists(ws):
        return {"error": "No graph yet."}
    G = load_graph(ws)
    src = _find_node(G, source)
    tgt = _find_node(G, target)
    if not src or not tgt:
        return {"error": f"Couldn't find both concepts. matched_source={src!r}, matched_target={tgt!r}"}
    if src == tgt:
        return {"error": "Source and target matched the same node — try more specific terms."}

    try:
        path = nx.shortest_path(G, src, tgt)
    except nx.NetworkXNoPath:
        return {
            "error": f"No path between “{G.nodes[src].get('label', src)}” and “{G.nodes[tgt].get('label', tgt)}”.",
            "matched_source": G.nodes[src].get("label", src),
            "matched_target": G.nodes[tgt].get("label", tgt),
        }

    hops = []
    for i, nid in enumerate(path):
        nd = G.nodes[nid]
        node = {
            "id": nid,
            "label": nd.get("label", nid),
            "source_file": nd.get("source_file"),
            "community_label": nd.get("community_label"),
        }
        if i < len(path) - 1:
            ed = G.edges[nid, path[i + 1]]
            node["out_relation"] = ed.get("relation")
            node["out_confidence"] = ed.get("confidence")
            node["out_confidence_score"] = ed.get("confidence_score")
        hops.append(node)
    return {
        "matched_source": G.nodes[src].get("label", src),
        "matched_target": G.nodes[tgt].get("label", tgt),
        "hop_count": len(path) - 1,
        "path": hops,
    }


def _cross_document_link(G: nx.Graph, max_pairs: int = 80) -> dict[str, Any]:
    """Ask Claude to identify entity-equivalence pairs across documents.

    Returns a dict with `edges_added`, `pairs` (raw), and `meta` (token usage).
    Adds INFERRED `same_as` / `semantically_similar_to` edges to G in-place.
    """
    client = _anthropic_client()
    if not client:
        return {"edges_added": 0, "pairs": [], "meta": {"error": "no API key"}}

    # Build (id, label, source_file) tuples grouped by source_file.
    by_source: dict[str, list[tuple[str, str]]] = {}
    for nid, ndata in G.nodes(data=True):
        src = ndata.get("source_file") or "(unknown)"
        by_source.setdefault(src, []).append((nid, ndata.get("label") or nid))

    if len(by_source) < 2:
        return {"edges_added": 0, "pairs": [], "meta": {"reason": "only one source document"}}

    # Compose the linker prompt.
    sections = []
    for src, items in by_source.items():
        lines = [f"  - id={nid} :: {label[:140]}" for nid, label in items]
        sections.append(f"## {src} ({len(items)} concepts)\n" + "\n".join(lines))

    prompt = f"""\
Below are extracted concepts from {len(by_source)} documents. Each concept has an id and a label.

Your task: identify pairs of concepts ACROSS DIFFERENT DOCUMENTS that refer to the same real-world
entity, decision, or closely related idea. The purpose is to densify a knowledge graph that's
currently fragmented across documents.

Rules:
- Only pair concepts from DIFFERENT documents (the part before the :: differs).
- Use relation "same_as" when both concepts unambiguously refer to the same entity (e.g. same
  company, same product, same regulation, same dated event). confidence_score: 0.85-0.98.
- Use relation "semantically_similar_to" when concepts are related/overlapping but not identical
  (e.g. one is a sub-concept, both describe a shared theme). confidence_score: 0.6-0.8.
- Aim for {max_pairs // 2}-{max_pairs} pairs total. Quality over quantity. Skip trivial pairs.
- Prefer high-degree concepts (named entities, decisions, recommendations) over generic phrases.

Concepts:
{chr(10).join(sections)}

Output ONLY this JSON, no other text:
{{"pairs":[{{"node_a":"id_from_doc_X","node_b":"id_from_doc_Y","relation":"same_as","confidence_score":0.9,"reason":"both refer to..."}}]}}
"""

    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_LINK_MODEL", "claude-sonnet-4-6"),
            max_tokens=int(os.environ.get("GRAPHIFY_LINK_MAX_TOKENS", "8000")),
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return {"edges_added": 0, "pairs": [], "meta": {"error": str(exc)}}

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    usage = getattr(msg, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
    out_tok = getattr(usage, "output_tokens", 0) if usage else 0

    parsed = _parse_extraction_json(text)
    pairs = parsed.get("pairs", []) if parsed else []

    valid_ids = set(G.nodes)
    added = 0
    accepted: list[dict] = []
    for p in pairs[:max_pairs]:
        a, b = p.get("node_a"), p.get("node_b")
        if a not in valid_ids or b not in valid_ids or a == b:
            continue
        # Skip pairs where both nodes share a source_file (LLM occasionally pairs within a doc).
        if G.nodes[a].get("source_file") == G.nodes[b].get("source_file"):
            continue
        if G.has_edge(a, b):
            continue  # already linked
        relation = p.get("relation") or "semantically_similar_to"
        if relation not in {"same_as", "semantically_similar_to"}:
            relation = "semantically_similar_to"
        score = float(p.get("confidence_score") or 0.7)
        G.add_edge(
            a, b,
            relation=relation,
            confidence="INFERRED",
            confidence_score=max(0.0, min(1.0, score)),
            source_file="cross-document linker",
            source_location=None,
            weight=1.0,
        )
        accepted.append({
            "source": a,
            "target": b,
            "source_label": G.nodes[a].get("label", a),
            "target_label": G.nodes[b].get("label", b),
            "relation": relation,
            "confidence_score": score,
            "reason": p.get("reason"),
        })
        added += 1

    return {
        "edges_added": added,
        "pairs": accepted,
        "meta": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "model": os.environ.get("GRAPHIFY_LINK_MODEL", "claude-sonnet-4-6"),
            "total_pair_candidates": len(pairs),
        },
    }


def link_documents(ws: Workspace) -> dict[str, Any]:
    """Run cross-document linking on the current graph, then re-cluster + re-label."""
    if not graph_exists(ws):
        return {"error": "No graph yet."}
    with ws.pipeline_lock:
        data = json.loads(ws.graph_file.read_text())
        G = json_graph.node_link_graph(data, edges="links")

        before_edges = G.number_of_edges()
        before_components = nx.number_connected_components(G)

        link_result = _cross_document_link(G)

        # Re-cluster + re-label so the new edges actually merge communities.
        communities = cluster_graph(G)
        cohesion = score_all(G, communities)
        labels = _auto_label_communities(G, communities)
        for cid, members in communities.items():
            for nid in members:
                if nid in G.nodes:
                    G.nodes[nid]["community_label"] = labels.get(cid)

        # Defensive: re-ensure the output dir right before write.
        ws.graph_file.parent.mkdir(parents=True, exist_ok=True)
        to_json(G, communities, str(ws.graph_file))
        _stamp_graph_provenance(ws, kind="recluster")

        insights = {
            "communities": {str(k): v for k, v in communities.items()},
            "community_labels": {str(k): v for k, v in labels.items()},
            "cohesion": {str(k): v for k, v in cohesion.items()},
            "gods": god_nodes(G),
            "surprises": surprising_connections(G, communities),
            "questions": suggest_questions(G, communities, labels),
        }
        ws.insights_file.write_text(json.dumps(insights, indent=2))

        # Re-cluster may have changed community labels on existing nodes;
        # only NEW nodes get embedded (model + dim must match the saved index).
        try:
            import embeddings as _emb
            _emb.update_index(ws, G)
        except Exception:
            pass

        return {
            "edges_added": link_result["edges_added"],
            "edges_before": before_edges,
            "edges_after": G.number_of_edges(),
            "components_before": before_components,
            "components_after": nx.number_connected_components(G),
            "communities_after": len(communities),
            "pairs": link_result["pairs"][:25],  # cap response payload
            "meta": link_result["meta"],
        }


# --- Rich query layer (LLM-routed, used by the Conversations tab) -------------

def _route_to_entry_nodes(
    G: nx.Graph,
    question: str,
    history_text: str = "",
    max_nodes: int = 5,
    ws: "Workspace | None" = None,
) -> dict[str, Any]:
    """Ask Claude to pick semantically relevant entry-point nodes for a question.

    Returns {"node_ids": [...], "reasoning": "...", "needs_graph": bool, "tokens": {...}}.
    If needs_graph=False, the LLM judged the question answerable without the corpus.
    """
    client = _anthropic_client()
    if not client or G.number_of_nodes() == 0:
        return {"node_ids": [], "reasoning": "no LLM available or empty graph", "needs_graph": False}

    # Pack node labels with id, degree, community_label. We trim very-low-degree
    # leaves first if the list gets large, but keep all nodes for normal corpora.
    nodes_payload = []
    for nid, ndata in G.nodes(data=True):
        nodes_payload.append({
            "id": nid,
            "label": (ndata.get("label") or nid)[:160],
            "src": ndata.get("source_file") or "",
            "community": ndata.get("community_label") or "",
            "deg": G.degree(nid),
        })

    # Vector-first ordering when an embeddings index is available — surfaces
    # semantically-relevant nodes the router would otherwise miss because they
    # sit below the degree-sort cutoff. Falls back to degree-sort when no
    # embedding provider is configured (graceful no-op via embeddings module).
    vector_ranked_ids: list[str] = []
    try:
        if ws is not None:
            import embeddings as _emb
            if _emb.is_available():
                hits = _emb.top_k(ws, question, k=80)
                vector_ranked_ids = [nid for nid, _score in hits if nid in G.nodes]
    except Exception:
        vector_ranked_ids = []

    if vector_ranked_ids:
        # Bring vector-relevant nodes to the front; keep the rest degree-sorted
        # behind them as fallback context. Cap to ~250 either way.
        by_id = {n["id"]: n for n in nodes_payload}
        front = [by_id[nid] for nid in vector_ranked_ids if nid in by_id]
        rest = [n for n in sorted(nodes_payload, key=lambda x: x["deg"], reverse=True)
                if n["id"] not in {f["id"] for f in front}]
        nodes_payload = (front + rest)[:250]
    else:
        # Sort by degree desc so the most-connected nodes appear first (helps Claude).
        nodes_payload.sort(key=lambda n: n["deg"], reverse=True)
        nodes_payload = nodes_payload[:250]

    listing = "\n".join(
        f"- id={n['id']} :: deg={n['deg']:2d} :: comm={n['community']} :: {n['label']}"
        for n in nodes_payload
    )

    history_block = f"\n\nPrior turns in this conversation:\n{history_text}\n" if history_text else ""

    prompt = f"""\
You are routing a user's question to entry points in a knowledge graph built from documents.

The user asked: {question}{history_block}

Below are the nodes in the graph (id, degree, community label, node label). Your job:
1. Decide if the question needs the graph at all. Set `needs_graph` accordingly. Casual chat,
   pure general-knowledge questions, or follow-ups that don't reference corpus content can
   often be answered without the graph.
2. If `needs_graph` is true, pick 1–{max_nodes} node ids that are the best entry points for a
   BFS traversal. Pick by semantic intent, not literal word overlap. Prefer high-degree nodes
   when the question is broad ("what are the strongest ideas?"), specific named nodes when the
   question is narrow ("what's the deadline for the EU AI Act?").

Graph nodes:
{listing}

Return ONLY this JSON:
{{"needs_graph": true, "node_ids": ["id1", "id2"], "reasoning": "one sentence on why these"}}
"""

    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_ROUTER_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return {"node_ids": [], "reasoning": f"router error: {exc}", "needs_graph": True}

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    usage = getattr(msg, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
    out_tok = getattr(usage, "output_tokens", 0) if usage else 0

    parsed = _parse_extraction_json(text)
    raw_ids = parsed.get("node_ids", []) if parsed else []
    valid = [nid for nid in raw_ids if nid in G.nodes][:max_nodes]
    return {
        "node_ids": valid,
        "reasoning": parsed.get("reasoning", "") if parsed else "",
        "needs_graph": parsed.get("needs_graph", True) if parsed else True,
        "tokens": {"input": in_tok, "output": out_tok},
    }


def _bfs_subgraph(G: nx.Graph, start_nodes: list[str], depth: int = 3, max_nodes: int = 50) -> tuple[set[str], list[tuple[str, str]]]:
    """Return (visited, edges) for a depth-limited BFS, bounded by max_nodes."""
    visited: set[str] = set(start_nodes)
    edges: list[tuple[str, str]] = []
    frontier: set[str] = set(start_nodes)
    for _ in range(depth):
        if len(visited) >= max_nodes:
            break
        next_frontier: set[str] = set()
        for n in frontier:
            for neighbor in G.neighbors(n):
                if neighbor not in visited and len(visited) + len(next_frontier) < max_nodes:
                    next_frontier.add(neighbor)
                    edges.append((n, neighbor))
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, edges


def rich_query(
    ws: Workspace,
    question: str,
    history_text: str = "",
    intent_instruction: str = "",
    rubric_body: str = "",
    memory_block: str = "",
    inference_strategy: str = "none",
    web_grounding: bool = False,
    answer_model: str | None = None,
) -> dict[str, Any]:
    """LLM-led query pipeline. Returns the answer + traces (router, subgraph, citations).

    Used by the Conversations tab. The graph is one data source; the synthesizer is free to
    use general knowledge when the graph is silent, but must flag which mode it is in.

    `intent_instruction` and `rubric_body` are optional system-prompt enrichments — they
    shape the answer's style and applicable evaluation rules.
    """
    if not graph_exists(ws):
        # No graph yet — fall back to a plain LLM answer for casual chat.
        return _plain_llm_answer(question, history_text, has_graph=False)

    G = load_graph(ws)
    route = _route_to_entry_nodes(G, question, history_text, ws=ws)

    subgraph_payload = None
    rendered = ""
    visited: set[str] = set()

    if route.get("needs_graph", True) and route.get("node_ids"):
        visited, edges = _bfs_subgraph(G, route["node_ids"])
        sub_nodes_meta = []
        for nid in visited:
            d = G.nodes[nid]
            sub_nodes_meta.append({
                "id": nid,
                "label": d.get("label", nid),
                "source_file": d.get("source_file"),
                "community_label": d.get("community_label"),
                "extracted_at": d.get("extracted_at"),
                "is_entry": nid in route["node_ids"],
            })
        sub_edges_meta = []
        for u, v in edges:
            if u in visited and v in visited and G.has_edge(u, v):
                ed = G.edges[u, v]
                sub_edges_meta.append({
                    "source": u,
                    "target": v,
                    "relation": ed.get("relation"),
                    "confidence": ed.get("confidence"),
                })
        subgraph_payload = {"nodes": sub_nodes_meta, "edges": sub_edges_meta}

        # Build a text rendering for the synthesizer prompt.
        lines = [f"Entry nodes: {', '.join(G.nodes[n].get('label', n) for n in route['node_ids'])}"]
        for n in visited:
            d = G.nodes[n]
            lines.append(f"  NODE {d.get('label', n)} [src={d.get('source_file', '')}]")
        for u, v in edges:
            if u in visited and v in visited:
                ed = G.edges[u, v]
                lines.append(
                    f"  EDGE {G.nodes[u].get('label', u)} --{ed.get('relation', '')} "
                    f"[{ed.get('confidence', '')}]--> {G.nodes[v].get('label', v)}"
                )
        rendered = "\n".join(lines)

        # Merge the user-curated refinements layer on top of the rendered
        # subgraph. Pure read-time merge — graph.json is untouched. The LLM
        # sees both the original extraction and the human override side-by-side
        # and is instructed (via the REFINEMENTS block) to honor refinements
        # over the graph when they conflict.
        try:
            import kb_corrections as _kbc
            node_label_map = {nid: G.nodes[nid].get("label", nid) for nid in visited}
            rendered = _kbc.apply_corrections_to_subgraph(rendered, ws, node_id_to_label=node_label_map)
        except Exception:
            # Refinements must never break the answer pipeline.
            pass

    answer = _run_inference_strategy(
        strategy=inference_strategy,
        question=question,
        history_text=history_text,
        rendered_subgraph=rendered,
        intent_instruction=intent_instruction,
        rubric_body=rubric_body,
        memory_block=memory_block,
        web_grounding=web_grounding,
        answer_model=answer_model,
    )

    return {
        "answer": answer.get("text", ""),
        "answer_tokens": answer.get("tokens", {}),
        "inference": {
            "strategy": inference_strategy,
            "steps": answer.get("steps", []),
        },
        "web_sources": answer.get("web_sources", []),
        "gaps": answer.get("gaps", []),
        "router": {
            "needs_graph": route.get("needs_graph"),
            "entry_node_ids": route.get("node_ids", []),
            "entry_node_labels": [G.nodes[n].get("label", n) for n in route.get("node_ids", []) if n in G.nodes],
            "reasoning": route.get("reasoning", ""),
            "tokens": route.get("tokens", {}),
        },
        "subgraph": subgraph_payload,
        "grounded": bool(visited),
    }


def _synthesize_with_history(
    question: str,
    history_text: str,
    rendered_subgraph: str,
    intent_instruction: str = "",
    rubric_body: str = "",
    memory_block: str = "",
    web_grounding: bool = False,
    override_system: str | None = None,
    override_user: str | None = None,
    answer_model: str | None = None,
) -> dict[str, Any]:
    """The synthesizer call: blend conversation history + subgraph + general knowledge.

    When web_grounding is True, the model is given the Anthropic web_search tool and asked
    to verify time-sensitive claims against the live web before answering.
    """
    client = _anthropic_client()
    if not client:
        return {"text": "(no LLM available — set ANTHROPIC_API_KEY)", "tokens": {}}

    base_rules = (
        "You answer research questions by combining a knowledge-graph subgraph with general knowledge.\n\n"
        "House style — CLEAR, CONCISE, COHERENT, COMPLETE:\n"
        "- Lead with one bold line starting **TL;DR:** — the single most important takeaway in ≤ 25 words.\n"
        "- After the TL;DR: **3–5 short sentences** OR up to 6 bullets. Never both. No preamble.\n"
        "- No filler ('it's worth noting', 'arguably', 'in summary'). No restating the question.\n"
        "- Numbers, names, dates over generalities. One idea per sentence/bullet (≤ 22 words).\n"
        "- For casual one-line follow-ups, skip the TL;DR and answer in a sentence.\n"
        "PRESERVE — never drop load-bearing detail:\n"
        "- Every dollar amount, percentage, ratio, date, deadline, named entity.\n"
        "- Every source_file citation and web attribution.\n"
        "If brevity conflicts with a load-bearing fact, KEEP THE FACT and trim prose instead.\n\n"
        "Grounding rules:\n"
        "- When a fact comes from the subgraph, cite the source_file in parens.\n"
        "- When using general knowledge, prefix with 'general knowledge:' so the source is clear.\n"
        "- If subgraph and general knowledge conflict, trust the subgraph.\n\n"
        + _GAP_PROMPT_INSTRUCTIONS
    )
    if web_grounding:
        base_rules += (
            "\n- You have a web_search tool. Use it to verify TIME-SENSITIVE claims (dates, "
            "company status, regulatory deadlines, fundraising, current product availability) "
            "before stating them. Do NOT search for things the corpus already nails down — only "
            "for current-world status where staleness matters. Cite web sources separately from "
            "corpus citations (e.g. 'web: example.com')."
        )
    memory_top = f"{memory_block}\n\n" if memory_block else ""
    intent_block = f"\n\n### Conversation intent\n{intent_instruction}" if intent_instruction else ""
    rubric_block = f"\n\n### Evaluation rubric (apply these rules)\n{rubric_body}" if rubric_body else ""

    if rendered_subgraph:
        system = memory_top + base_rules + intent_block + rubric_block
        user_content = (
            (f"Conversation so far:\n{history_text}\n\n" if history_text else "")
            + f"New question: {question}\n\n"
            + f"Relevant subgraph from the corpus:\n{rendered_subgraph}\n\n"
            + "Answer:"
        )
    else:
        system = (
            memory_top
            + "Research assistant. The knowledge graph isn't relevant for this turn — "
            "answer from general knowledge + prior conversation. **Be concise: 2–4 short "
            "sentences max, no preamble, no filler.** If the user clearly needs corpus "
            "content but the graph couldn't help, say so in one line."
        ) + intent_block + rubric_block

    if override_system is not None:
        system = override_system
    if override_user is not None:
        user_content = override_user
        user_content = (
            (f"Conversation so far:\n{history_text}\n\n" if history_text else "")
            + f"New question: {question}"
        )

    chosen_model = answer_model or os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-sonnet-4-6")
    kwargs: dict[str, Any] = {
        "model": chosen_model,
        # Tight cap reinforces the "3–5 sentences" instruction. Web grounding
        # adds a small headroom for inline citations.
        "max_tokens": 1100 if web_grounding else 800,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
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
        return {"text": f"(synthesis failed: {exc})", "tokens": {}}

    text_parts = []
    web_sources: list[dict[str, str]] = []
    for b in msg.content:
        t = getattr(b, "type", None)
        if t == "text":
            text_parts.append(b.text)
        elif t == "web_search_tool_result":
            results = getattr(b, "content", None)
            if isinstance(results, list):
                for r in results:
                    title = getattr(r, "title", None) or ""
                    url = getattr(r, "url", None) or ""
                    if url:
                        web_sources.append({"title": title, "url": url})
    text = "\n".join(p for p in text_parts if p).strip()
    cleaned, gaps = _parse_gaps(text)

    usage = getattr(msg, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
    out_tok = getattr(usage, "output_tokens", 0) if usage else 0
    return {
        "text": cleaned,
        "tokens": {"input": in_tok, "output": out_tok},
        "web_sources": web_sources,
        "gaps": gaps,
    }


def render_graph_context(ws: Workspace, question: str, history_text: str = "") -> dict[str, Any]:
    """Find entry nodes via the router and render a small subgraph for context.

    Used by features that want graph grounding without running the full synthesizer
    (e.g. scenario simulation, where each persona consumes the same context).
    """
    if not graph_exists(ws):
        return {"rendered": "", "entry_node_labels": []}
    G = load_graph(ws)
    route = _route_to_entry_nodes(G, question, history_text, ws=ws)
    if not (route.get("needs_graph", True) and route.get("node_ids")):
        return {"rendered": "", "entry_node_labels": []}
    visited, edges = _bfs_subgraph(G, route["node_ids"])
    lines = []
    for n in visited:
        d = G.nodes[n]
        lines.append(f"  NODE {d.get('label', n)} [src={d.get('source_file', '')}]")
    for u, v in edges:
        if u in visited and v in visited and G.has_edge(u, v):
            ed = G.edges[u, v]
            lines.append(
                f"  EDGE {G.nodes[u].get('label', u)} --{ed.get('relation', '')} "
                f"[{ed.get('confidence', '')}]--> {G.nodes[v].get('label', v)}"
            )
    return {
        "rendered": "\n".join(lines),
        "entry_node_labels": [G.nodes[n].get("label", n) for n in route["node_ids"] if n in G.nodes],
        "router_reasoning": route.get("reasoning", ""),
    }


# --- Inference strategies (OptILLM-style) ----------------------------------

_INFERENCE_STRATEGIES = {"none", "reflection", "cove", "best_of_3"}


def _run_inference_strategy(
    *,
    strategy: str,
    question: str,
    history_text: str,
    rendered_subgraph: str,
    intent_instruction: str,
    rubric_body: str,
    memory_block: str,
    web_grounding: bool,
    answer_model: str | None = None,
) -> dict[str, Any]:
    """Run the synthesizer under the chosen inference strategy.

    Always returns {"text", "tokens", "web_sources", "steps"} where steps is a list of
    {"label": str, "tokens": {...}} entries for UI display.
    """
    strategy = strategy if strategy in _INFERENCE_STRATEGIES else "none"

    def synth(extra_system: str | None = None, extra_user: str | None = None, web: bool = web_grounding) -> dict[str, Any]:
        return _synthesize_with_history(
            question, history_text, rendered_subgraph,
            intent_instruction=intent_instruction, rubric_body=rubric_body,
            memory_block=memory_block, web_grounding=web,
            override_system=extra_system, override_user=extra_user,
            answer_model=answer_model,
        )

    if strategy == "none":
        out = synth()
        return {
            "text": out.get("text", ""),
            "tokens": out.get("tokens", {}),
            "web_sources": out.get("web_sources", []),
            "gaps": out.get("gaps", []),
            "steps": [{"label": "single-pass", "tokens": out.get("tokens", {})}],
        }

    if strategy == "reflection":
        # Step 1: draft. Step 2: critique. Step 3: revise.
        draft = synth()
        critique_user = (
            f"Below is a draft answer to the user's question. Critique it briefly:\n"
            f"- What might be wrong, missing, or weakly supported?\n"
            f"- Which claims need a more concrete citation?\n"
            f"- Are there alternative interpretations the user should consider?\n\n"
            f"User question: {question}\n\nDraft:\n{draft.get('text','')}\n\n"
            f"Return 3-5 bullet critiques, then a single line: 'REVISE: <yes|no>'."
        )
        critique = _synthesize_with_history(
            "(critique)", "", "",
            override_system="You are a careful research-quality reviewer. Be specific.",
            override_user=critique_user, web_grounding=False,
        )
        if "REVISE: no" in (critique.get("text", "") or "").lower():
            return {
                "text": draft.get("text", ""),
                "tokens": _merge_tokens(draft.get("tokens"), critique.get("tokens")),
                "web_sources": draft.get("web_sources", []),
                "gaps": draft.get("gaps", []),
                "steps": [
                    {"label": "draft", "tokens": draft.get("tokens", {})},
                    {"label": "critique (no revise)", "tokens": critique.get("tokens", {})},
                ],
            }
        revise_user = (
            f"Revise the draft to address the critique. Keep all citations. Stay concise.\n\n"
            f"User question: {question}\n\nDraft:\n{draft.get('text','')}\n\n"
            f"Critique:\n{critique.get('text','')}\n\nRevised answer:"
        )
        revised = synth(extra_user=revise_user, web=False)
        all_sources = list({s["url"]: s for s in draft.get("web_sources", []) + revised.get("web_sources", [])}.values())
        return {
            "text": revised.get("text", "") or draft.get("text", ""),
            "tokens": _merge_tokens(draft.get("tokens"), critique.get("tokens"), revised.get("tokens")),
            "web_sources": all_sources,
            "gaps": revised.get("gaps", []) or draft.get("gaps", []),
            "steps": [
                {"label": "draft", "tokens": draft.get("tokens", {})},
                {"label": "critique", "tokens": critique.get("tokens", {})},
                {"label": "revise", "tokens": revised.get("tokens", {})},
            ],
        }

    if strategy == "cove":
        # Chain-of-Verification: draft → generate verification questions → answer them → revise.
        draft = synth()
        verify_user = (
            f"Generate 3-5 factual verification questions about the draft below. Each question "
            f"should target a specific claim that could be wrong. Then answer each one using ONLY "
            f"what you can defend (cite source_file when from corpus, mark 'uncertain' otherwise).\n\n"
            f"User question: {question}\n\nDraft:\n{draft.get('text','')}\n\n"
            f"Format: Q1: ... A1: ... / Q2: ..."
        )
        verify = synth(extra_user=verify_user)
        revise_user = (
            f"Revise the draft using the verification answers. Keep correct claims, remove or hedge "
            f"those that didn't verify. Stay concise.\n\n"
            f"User question: {question}\n\nDraft:\n{draft.get('text','')}\n\n"
            f"Verification:\n{verify.get('text','')}\n\nRevised answer:"
        )
        revised = synth(extra_user=revise_user, web=False)
        all_sources = list({
            s["url"]: s for s in draft.get("web_sources", []) + verify.get("web_sources", []) + revised.get("web_sources", [])
        }.values())
        return {
            "text": revised.get("text", "") or draft.get("text", ""),
            "tokens": _merge_tokens(draft.get("tokens"), verify.get("tokens"), revised.get("tokens")),
            "web_sources": all_sources,
            "gaps": revised.get("gaps", []) or draft.get("gaps", []),
            "steps": [
                {"label": "draft", "tokens": draft.get("tokens", {})},
                {"label": "verify", "tokens": verify.get("tokens", {})},
                {"label": "revise", "tokens": revised.get("tokens", {})},
            ],
        }

    if strategy == "best_of_3":
        # Sample 3 candidates, then ask the model to pick the best.
        candidates = [synth() for _ in range(3)]
        joined = "\n\n".join(f"### Candidate {i+1}\n{c.get('text','')}" for i, c in enumerate(candidates))
        pick_user = (
            f"Three candidate answers to the user's question are below. Pick the BEST one based on:\n"
            f"- factual accuracy and grounding\n"
            f"- usefulness and concreteness for the user\n"
            f"- clarity and concision\n\n"
            f"User question: {question}\n\n{joined}\n\n"
            f"Output ONLY the chosen candidate's full text (no preamble, no 'I chose Candidate X')."
        )
        picked = synth(extra_user=pick_user, web=False)
        all_sources = []
        seen_urls: set[str] = set()
        for c in candidates + [picked]:
            for s in c.get("web_sources", []):
                if s["url"] and s["url"] not in seen_urls:
                    seen_urls.add(s["url"])
                    all_sources.append(s)
        # Picked gaps win; fall back to the first candidate's gaps if the pick
        # step somehow returned none (rare — model usually echoes the block).
        picked_gaps = picked.get("gaps", []) or (candidates[0].get("gaps", []) if candidates else [])
        return {
            "text": picked.get("text", "") or candidates[0].get("text", ""),
            "tokens": _merge_tokens(*[c.get("tokens") for c in candidates], picked.get("tokens")),
            "web_sources": all_sources,
            "gaps": picked_gaps,
            "steps": (
                [{"label": f"sample {i+1}", "tokens": c.get("tokens", {})} for i, c in enumerate(candidates)]
                + [{"label": "pick", "tokens": picked.get("tokens", {})}]
            ),
        }

    # Fallback (shouldn't reach here)
    out = synth()
    return {
        "text": out.get("text", ""),
        "tokens": out.get("tokens", {}),
        "web_sources": out.get("web_sources", []),
        "gaps": out.get("gaps", []),
        "steps": [{"label": "single-pass (fallback)", "tokens": out.get("tokens", {})}],
    }


def _merge_tokens(*tokens: dict[str, Any] | None) -> dict[str, int]:
    total = {"input": 0, "output": 0}
    for t in tokens:
        if not t:
            continue
        total["input"] += int(t.get("input", 0) or 0)
        total["output"] += int(t.get("output", 0) or 0)
    return total


def _plain_llm_answer(question: str, history_text: str, has_graph: bool) -> dict[str, Any]:
    """No graph available — just LLM general-knowledge answer."""
    out = _synthesize_with_history(question, history_text, rendered_subgraph="")
    return {
        "answer": out["text"],
        "answer_tokens": out["tokens"],
        "router": {"needs_graph": False, "reasoning": "no graph available" if not has_graph else "router skipped"},
        "subgraph": None,
        "grounded": False,
    }


def fetch_url_to_workspace(
    ws: Workspace, url: str, author: str | None = None, contributor: str | None = None,
) -> dict[str, Any]:
    """Fetch a URL and save it to the workspace. Does NOT rebuild — caller
    is responsible. Quick (HTTP fetch + write to disk)."""
    try:
        from graphify.ingest import ingest as graphify_ingest
    except ImportError:
        return {"error": "graphify.ingest module not available."}

    ws.ensure_dirs()
    try:
        out = graphify_ingest(url, ws.raw_dir, author=author, contributor=contributor)
    except (ValueError, RuntimeError) as exc:
        return {"error": f"Ingestion failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Unexpected error: {exc}"}

    # Move it into the workspace's raw_dir if graphify wrote it elsewhere.
    saved_path = Path(out)
    if saved_path.parent != ws.raw_dir:
        target = ws.raw_dir / saved_path.name
        if saved_path.exists():
            saved_path.replace(target)
            saved_path = target

    return {"saved": saved_path.name}


def ingest_url(ws: Workspace, url: str, author: str | None = None, contributor: str | None = None) -> dict[str, Any]:
    """Legacy synchronous variant: fetch THEN rebuild."""
    out = fetch_url_to_workspace(ws, url, author=author, contributor=contributor)
    if "error" in out:
        return out
    return {**out, "result": rebuild_graph(ws)}


_STOP_WORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "does", "doesn", "did", "didn", "are", "aren", "the", "this", "that",
    "these", "those", "with", "from", "into", "about", "have", "has", "had",
    "should", "would", "could", "will", "your", "you", "ours", "their",
    "them", "they", "and", "but", "for", "not", "any", "all", "some", "much",
    "many", "tell", "give", "show", "find",
}


def _query_terms(question: str) -> list[str]:
    """Lowercase tokens, strip punctuation, drop stop-words + ultra-short words."""
    raw = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9'-]*", question.lower())
    return [t for t in raw if len(t) > 2 and t not in _STOP_WORDS]


def _top_degree_nodes(G: nx.Graph, k: int = 3) -> list[str]:
    return [nid for nid, _ in sorted(G.degree, key=lambda x: x[1], reverse=True)[:k]]


def query_graph(ws: Workspace, question: str, mode: str = "bfs", budget: int = 2000) -> dict[str, Any]:
    """Run a graph traversal and return the relevant subgraph for the question."""
    if not graph_exists(ws):
        return {
            "error": "No graph yet. Upload at least one document first.",
            "subgraph": {"nodes": [], "edges": []},
        }
    G = load_graph(ws)
    terms = _query_terms(question)

    # Rank nodes by term overlap with their label.
    scored: list[tuple[int, str]] = []
    if terms:
        for nid, ndata in G.nodes(data=True):
            label = (ndata.get("label") or "").lower()
            score = sum(1 for t in terms if t in label)
            if score > 0:
                scored.append((score, nid))
        scored.sort(reverse=True)

    start_nodes = [nid for _, nid in scored[:3]]
    fallback_used = False

    if not start_nodes:
        # No term-match: anchor at the most-connected nodes and let the
        # synthesizer answer from the broader graph context.
        start_nodes = _top_degree_nodes(G, k=3)
        fallback_used = True

    visited: set[str] = set()
    edges_collected: list[tuple[str, str]] = []

    if mode == "dfs":
        stack: list[tuple[str, int]] = [(n, 0) for n in reversed(start_nodes)]
        while stack:
            node, depth = stack.pop()
            if node in visited or depth > 6:
                continue
            visited.add(node)
            for neighbor in G.neighbors(node):
                if neighbor not in visited:
                    stack.append((neighbor, depth + 1))
                    edges_collected.append((node, neighbor))
    else:  # BFS, depth 3
        visited = set(start_nodes)
        frontier: set[str] = set(start_nodes)
        for _ in range(3):
            next_frontier: set[str] = set()
            for n in frontier:
                for neighbor in G.neighbors(n):
                    if neighbor not in visited:
                        next_frontier.add(neighbor)
                        edges_collected.append((n, neighbor))
            visited.update(next_frontier)
            frontier = next_frontier

    def relevance(nid: str) -> int:
        label = (G.nodes[nid].get("label") or "").lower()
        return sum(1 for t in terms if t in label)

    ranked = sorted(visited, key=relevance, reverse=True)

    sub_nodes = []
    for nid in ranked:
        d = G.nodes[nid]
        sub_nodes.append({
            "id": nid,
            "label": d.get("label", nid),
            "source_file": d.get("source_file"),
            "file_type": d.get("file_type"),
            "community": d.get("community"),
            "relevance": relevance(nid),
            "is_start": nid in start_nodes,
        })

    sub_edges = []
    for u, v in edges_collected:
        if u in visited and v in visited and G.has_edge(u, v):
            ed = G.edges[u, v]
            sub_edges.append({
                "source": u,
                "target": v,
                "relation": ed.get("relation"),
                "confidence": ed.get("confidence"),
                "confidence_score": ed.get("confidence_score"),
            })

    # Build a token-budgeted text rendering for the LLM-free summary.
    char_budget = budget * 4
    lines = [
        f"Traversal: {mode.upper()} | Start: "
        + ", ".join(G.nodes[n].get("label", n) for n in start_nodes)
        + f" | {len(visited)} nodes"
    ]
    for n in ranked:
        d = G.nodes[n]
        lines.append(f"  NODE {d.get('label', n)} [src={d.get('source_file', '')}]")
    for u, v in edges_collected:
        if u in visited and v in visited and G.has_edge(u, v):
            ed = G.edges[u, v]
            lines.append(
                f"  EDGE {G.nodes[u].get('label', u)} --{ed.get('relation', '')} "
                f"[{ed.get('confidence', '')}]--> {G.nodes[v].get('label', v)}"
            )
    rendered = "\n".join(lines)
    if len(rendered) > char_budget:
        rendered = rendered[:char_budget] + f"\n... (truncated at ~{budget} tokens)"

    return {
        "start_nodes": [G.nodes[n].get("label", n) for n in start_nodes],
        "mode": mode,
        "terms": terms,
        "fallback_used": fallback_used,
        "subgraph": {"nodes": sub_nodes, "edges": sub_edges},
        "rendered": rendered,
    }


# --- Synthesis gap-analysis -------------------------------------------------
#
# Every synthesizer call (Ask Graph + every Conversation turn + every Playbook
# step that calls into the synth helpers) emits a structured `gaps` list
# alongside the prose answer. The prompt instructs the model to wrap 1–4
# specific gap items in a `<gaps>…</gaps>` block at the end of its response;
# `_parse_gaps` extracts that block, strips it from the prose, and returns
# both. The UI renders the gap list as a distinct "What the brain doesn't
# know yet" block under every answer.
#
# Specificity is enforced via the prompt: each gap must name a concrete
# missing/stale/uncertain fact, NOT a generic "more research needed."

_GAP_PROMPT_INSTRUCTIONS = (
    "After your answer, append a structured gap-analysis block in this exact format:\n\n"
    "<gaps>\n"
    "- specific gap 1\n"
    "- specific gap 2\n"
    "</gaps>\n\n"
    "List 1–4 specific gaps. Each gap must name a concrete fact the corpus is "
    "missing, stale on, or only weakly supports — NEVER generic 'more research "
    "needed'. Good examples:\n"
    "- 'Corpus is silent on Q1 2026 revenue (most recent figure: Q3 2025)'\n"
    "- 'Cited deadline (Feb 2026) not verified against a current regulatory source'\n"
    "- 'Graph has no nodes for EU market exposure'\n"
    "- 'Only one source supports this claim; second corroboration absent'\n"
    "If the answer is so well-grounded that there are no meaningful gaps, "
    "output exactly: <gaps>none</gaps>"
)


_GAPS_RE = re.compile(r"<gaps>\s*(.*?)\s*</gaps>", re.DOTALL | re.IGNORECASE)


def _parse_gaps(text: str) -> tuple[str, list[str]]:
    """Pull a <gaps>…</gaps> block out of the model's response, return the
    cleaned prose and the parsed list. Robust to malformed output: missing
    block → empty list, no error."""
    if not text:
        return text, []
    m = _GAPS_RE.search(text)
    if not m:
        return text.strip(), []
    body = m.group(1).strip()
    cleaned = _GAPS_RE.sub("", text).strip()
    if not body or body.lower() == "none":
        return cleaned, []
    items: list[str] = []
    for raw in body.split("\n"):
        line = raw.strip().lstrip("-*•").strip()
        if line:
            items.append(line)
    return cleaned, items


def synthesize_answer(
    question: str,
    rendered: str,
    *,
    web_grounding: bool = False,
    rubric_body: str = "",
    memory_block: str = "",
) -> dict[str, Any] | None:
    """Turn the subgraph into a plain-language answer. Returns {"text",
    "web_sources", "gaps"} — `gaps` is a list of specific items the corpus
    is silent on / stale on / weakly supports.

    `rubric_body` and `memory_block` are folded into the system prompt so this
    LLM call honors the same framing rules + persistent memory as the
    Conversations and Playbooks paths. When web_grounding=True, the Anthropic
    web_search tool is offered so the model can verify time-sensitive claims.
    """
    if not rendered:
        return None
    client = _anthropic_client()
    if not client:
        return None
    model = os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-haiku-4-5-20251001")
    system = (
        "You answer questions using the knowledge graph subgraph provided. "
        "Cite source_file names when you quote a corpus fact. Do not invent edges. "
        "If the subgraph is insufficient, say so.\n\n"
        + _GAP_PROMPT_INSTRUCTIONS
    )
    if rubric_body:
        system += "\n\n=== RUBRIC (apply these framing rules to every answer) ===\n" + rubric_body
    if memory_block:
        system += "\n\n=== PERSISTENT MEMORY (durable user-provided context) ===\n" + memory_block
    if web_grounding:
        system += (
            " You have a web_search tool. Use it ONLY for time-sensitive facts "
            "(dates, current status, regulations, recent news) the corpus is silent on. "
            "Cite web sources as 'web: domain.com'."
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 900 if web_grounding else 600,
        "system": system,
        "messages": [{
            "role": "user",
            "content": (
                f"Question: {question}\n\nSubgraph (BFS traversal from the most relevant nodes):\n"
                f"{rendered}\n\nAnswer in 3-6 sentences."
            ),
        }],
    }
    if web_grounding:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "3")),
        }]
    msg = client.messages.create(**kwargs)
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
    text = "\n".join(p for p in text_parts if p).strip()
    if not text:
        return None
    cleaned, gaps = _parse_gaps(text)
    return {"text": cleaned, "web_sources": web_sources, "gaps": gaps}


def full_graph_json(ws: Workspace) -> dict[str, Any]:
    if not graph_exists(ws):
        return {"nodes": [], "links": []}
    return json.loads(ws.graph_file.read_text())


# ---- Local code-repo ingestion --------------------------------------------
#
# Copies code + docs from a local directory into ws.raw_dir/<repo-name>/...,
# preserving structure, then triggers a graph rebuild. graphify.detect walks
# raw_dir recursively, so nested files are picked up automatically.

_REPO_SKIP_DIRS = {
    # VCS / editor metadata
    ".git", ".hg", ".svn", ".idea", ".vscode", ".history",
    # Generic dependency / vendoring directories
    "node_modules", "vendor", "third_party", "third-party", "thirdparty",
    "external", "submodules",
    # Python venvs + caches
    "__pycache__", ".venv", "venv", "env", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", "htmlcov", ".eggs", "coverage",
    # JS / TS build + caches
    "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".astro",
    ".cache", ".parcel-cache", ".turbo", ".nx", ".expo",
    # Java / Kotlin / Scala / Android / .NET
    "target", ".gradle", ".mvn", ".bloop", ".metals", "bin", "obj",
    # iOS / macOS
    "Pods", "Carthage", "DerivedData", "xcuserdata",
    # Go / Dart / Flutter
    ".dart_tool", ".pub-cache",
    # CMake / C++ build
    "cmake-build-debug", "cmake-build-release", "_build",
    # Elixir / Erlang
    "deps", "_deps",
    # Infrastructure tooling caches
    ".terraform", ".terragrunt-cache", ".serverless",
    # Runtime / temp / logs
    "tmp", "temp", "logs",
    # Bazel
    "bazel-bin", "bazel-out", "bazel-testlogs",
}

# Allow extending the skip list at runtime without a code edit, e.g.:
#   GRAPHIFY_EXTRA_SKIP_DIRS=fixtures,__snapshots__,testdata
_extra_skip = os.environ.get("GRAPHIFY_EXTRA_SKIP_DIRS", "")
if _extra_skip:
    _REPO_SKIP_DIRS = _REPO_SKIP_DIRS | {
        s.strip() for s in _extra_skip.split(",") if s.strip()
    }

_REPO_CODE_EXTS = {
    ".py", ".pyi", ".ipynb",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh",
    ".m", ".mm", ".swift", ".rb", ".php", ".scala",
    ".clj", ".cljs", ".ex", ".exs", ".erl", ".hs", ".ml", ".mli",
    ".sh", ".bash", ".zsh", ".fish", ".sql",
    ".lua", ".dart", ".r", ".jl", ".vue", ".svelte",
}
_REPO_DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".adoc"}
_REPO_CONFIG_BASENAMES = {
    "Dockerfile", "Makefile", "Rakefile", "Gemfile", "Procfile", "CMakeLists.txt",
}

# Filename patterns to skip even when the extension matches a tracked language.
# These are generated/vendored artifacts that would bloat the graph without
# adding signal: lockfiles, minified bundles, source maps, generated protobuf.
_REPO_SKIP_BASENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Pipfile.lock", "poetry.lock", "Gemfile.lock", "Cargo.lock",
    "composer.lock", "go.sum", "mix.lock", "flake.lock",
}
_REPO_SKIP_SUFFIXES = (
    ".min.js", ".min.css", ".min.mjs",
    ".bundle.js", ".bundle.css",
    ".js.map", ".css.map", ".d.ts.map",
    ".generated.go", ".generated.py", ".generated.ts",
    ".pb.go", ".pb.cc", ".pb.h",
    "_pb2.py", "_pb2_grpc.py",
    ".lock",
)

_REPO_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB per file


def _load_ignore_spec(src: Path) -> Any:
    """Build a PathSpec from the repo's ignore files. Patterns are combined
    additively — anything matched by any source is skipped.

    Sources, in order:
      1. `.gitignore` (root of the source path) — standard git ignores.
      2. `.git/info/exclude` — local-only git ignores.
      3. `.kbignore` (root of the source path) — InnoBrain-specific extras.
         Same syntax as .gitignore (gitwildmatch). Use this for things you
         want indexed by git but NOT by the KB (e.g. large fixtures, vendored
         libraries, test snapshots).

    Returns None when pathspec isn't installed or no ignore patterns exist.
    """
    try:
        import pathspec
    except ImportError:
        return None
    patterns: list[str] = []
    for candidate in (
        src / ".gitignore",
        src / ".git" / "info" / "exclude",
        src / ".kbignore",
    ):
        if not candidate.exists():
            continue
        try:
            patterns.extend(candidate.read_text(errors="replace").splitlines())
        except OSError:
            continue
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _safe_repo_name(name: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name.strip())
    safe = safe.strip(".")
    return safe or "repo"


def _is_indexable_file(path: Path) -> bool:
    name_lower = path.name.lower()
    # Skip dotfiles (e.g. .env, .gitignore, .DS_Store, .npmrc). Almost always
    # config/metadata that adds noise without entities.
    if path.name.startswith("."):
        return False
    # Always-skip patterns: lockfiles, minified bundles, source maps, generated
    # protobuf. These match by basename or suffix regardless of directory.
    if path.name in _REPO_SKIP_BASENAMES:
        return False
    if any(name_lower.endswith(suf) for suf in _REPO_SKIP_SUFFIXES):
        return False
    if path.suffix.lower() in _REPO_CODE_EXTS:
        return True
    if path.suffix.lower() in _REPO_DOC_EXTS:
        return True
    if path.name in _REPO_CONFIG_BASENAMES:
        return True
    return False


def copy_repo_files(ws: Workspace, path: str, name: str | None = None) -> dict[str, Any]:
    """Copy code + docs from a local directory into the workspace. DOES NOT
    rebuild the graph — caller is responsible for that.

    Files are placed under `ws.raw_dir / <repo_name> / <relative-path>` so the
    detect/extract pipeline (which already walks raw_dir recursively) picks
    them up alongside any other uploaded docs.

    Returns: {repo, copied, skipped, bytes, ...} or {error, ...}.
    Synchronous and quick (seconds, not minutes) — safe to call from a
    FastAPI handler before enqueueing the rebuild on a background thread.
    """
    src = Path(path).expanduser()
    try:
        src = src.resolve(strict=True)
    except (OSError, FileNotFoundError):
        return {"error": f"Path does not exist: {path}"}
    if not src.is_dir():
        return {"error": f"Path is not a directory: {src}"}

    repo_name = _safe_repo_name(name or src.name)
    ws.ensure_dirs()
    dest = ws.raw_dir / repo_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied = 0
    skipped_dirs = 0
    skipped_files = 0
    skipped_gitignore = 0
    total_bytes = 0
    too_large: list[str] = []

    # Load .gitignore + .kbignore once so we don't re-read per directory.
    # Returns None when no ignore files exist or pathspec isn't available; in
    # either case we fall back to just the hard-coded _REPO_SKIP_DIRS + dotfile
    # rules.
    ignore_spec = _load_ignore_spec(src)

    def _is_gitignored(rel_posix: str, is_dir: bool) -> bool:
        if ignore_spec is None:
            return False
        # Append trailing slash for directories so dir-only patterns match.
        return ignore_spec.match_file(rel_posix + "/" if is_dir else rel_posix)

    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel_dir = Path(dirpath).relative_to(src)

        # Prune dirs in-place so os.walk doesn't descend into them. Three rules:
        #   1. Dot-prefix (covers .git, .github, .vscode, …)
        #   2. Hard-coded dependency/build directory list
        #   3. .gitignore-matched paths
        keep_dirs: list[str] = []
        for d in dirnames:
            if d.startswith(".") or d in _REPO_SKIP_DIRS:
                skipped_dirs += 1
                continue
            rel_sub = (rel_dir / d).as_posix()
            if _is_gitignored(rel_sub, is_dir=True):
                skipped_gitignore += 1
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for fname in filenames:
            p = Path(dirpath) / fname
            if not _is_indexable_file(p):
                skipped_files += 1
                continue
            rel_file = (rel_dir / fname).as_posix()
            if _is_gitignored(rel_file, is_dir=False):
                skipped_gitignore += 1
                continue
            try:
                size = p.stat().st_size
            except OSError:
                skipped_files += 1
                continue
            if size > _REPO_MAX_FILE_BYTES:
                too_large.append(str(p.relative_to(src)))
                skipped_files += 1
                continue
            rel = p.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(p, target)
            except OSError:
                skipped_files += 1
                continue
            copied += 1
            total_bytes += size

    if copied == 0:
        shutil.rmtree(dest, ignore_errors=True)
        return {
            "error": "No indexable files found (expected code or markdown/text). "
                     "Check the path and that the directory isn't entirely .git/node_modules.",
            "copied": 0,
        }

    return {
        "repo": repo_name,
        "source_path": str(src),
        "copied": copied,
        "skipped_dirs": skipped_dirs,
        "skipped_files": skipped_files,
        "skipped_ignored": skipped_gitignore,
        "ignore_active": ignore_spec is not None,
        "kbignore_present": (src / ".kbignore").exists(),
        "too_large_sample": too_large[:5],
        "bytes": total_bytes,
    }


def ingest_repo(ws: Workspace, path: str, name: str | None = None) -> dict[str, Any]:
    """Legacy synchronous variant: copy files THEN rebuild. New callers should
    use copy_repo_files() + a background rebuild via index_jobs.start()."""
    copy_result = copy_repo_files(ws, path, name)
    if "error" in copy_result:
        return copy_result
    return {**copy_result, "result": rebuild_graph(ws)}


def list_repos(ws: Workspace) -> list[dict[str, Any]]:
    """Top-level subdirectories under raw/ are treated as ingested repos."""
    out: list[dict[str, Any]] = []
    if not ws.raw_dir.exists():
        return out
    for p in sorted(ws.raw_dir.iterdir()):
        if not p.is_dir():
            continue
        n_files = 0
        total = 0
        latest = p.stat().st_mtime
        for sub in p.rglob("*"):
            if sub.is_file():
                n_files += 1
                try:
                    st = sub.stat()
                    total += st.st_size
                    if st.st_mtime > latest:
                        latest = st.st_mtime
                except OSError:
                    pass
        out.append({
            "name": p.name,
            "file_count": n_files,
            "bytes": total,
            "modified": latest,
        })
    return out


def delete_repo(ws: Workspace, name: str) -> bool:
    target = ws.raw_dir / _safe_repo_name(name)
    if not target.exists() or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
