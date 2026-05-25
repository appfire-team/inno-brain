"""Vector index for graph nodes — sits alongside graphify, doesn't replace it.

The graph is still the source of truth for entities, edges, and communities. This
module adds a cheap semantic-retrieval layer used to pick *better* entry points
for BFS than degree-sort can. Sources: gbrain proved hybrid retrieval lifts P@5
by +30 over keyword/degree baselines.

Storage: one file per workspace at `<workspace>/graphify-out/embeddings.npz`.
Single file holds: node_ids (list), vectors (np.ndarray [N, D]), and a metadata
dict (model name, dimension, created/updated timestamps).

Provider: VOYAGE_API_KEY first (Anthropic-recommended), then OPENAI_API_KEY.
If neither is set the module is a graceful no-op — caller falls back to the
existing degree-sort + LLM router path.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

try:
    import numpy as np  # bundled via networkx; always available in our deps
except ImportError:  # pragma: no cover — defensive
    np = None  # type: ignore

from workspaces import Workspace


# Tuned for the two providers we support.
_VOYAGE_MODEL = "voyage-3.5-lite"  # 1024d, cheap, fast
_OPENAI_MODEL = "text-embedding-3-small"  # 1536d
_BATCH_SIZE = 64
_MAX_NODE_TEXT_CHARS = 600  # bound per-node cost; labels + a short context window


def _index_path(ws: Workspace) -> Path:
    ws.ensure_dirs()
    return ws.out_dir / "embeddings.npz"


def _meta_path(ws: Workspace) -> Path:
    return ws.out_dir / "embeddings.meta.json"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _provider() -> dict[str, Any] | None:
    """Return {kind, model, dim, embed_fn} for the active provider, or None.

    embed_fn: callable taking list[str] → list[list[float]]. Caller handles
    batching at the higher level so the function stays thin.
    """
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    if voyage_key:
        try:
            import voyageai  # type: ignore
        except ImportError:
            return None
        client = voyageai.Client(api_key=voyage_key)

        def voyage_embed(texts: list[str]) -> list[list[float]]:
            r = client.embed(texts, model=_VOYAGE_MODEL, input_type="document")
            return list(r.embeddings)

        return {"kind": "voyage", "model": _VOYAGE_MODEL, "dim": 1024, "embed": voyage_embed}

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return None
        client = OpenAI(api_key=openai_key)

        def openai_embed(texts: list[str]) -> list[list[float]]:
            r = client.embeddings.create(model=_OPENAI_MODEL, input=texts)
            return [d.embedding for d in r.data]

        return {"kind": "openai", "model": _OPENAI_MODEL, "dim": 1536, "embed": openai_embed}

    return None


def is_available() -> bool:
    """Cheap check used by callers to decide whether to take the vector path."""
    return _provider() is not None and np is not None


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

def _load_index(ws: Workspace) -> tuple[list[str], "np.ndarray | None", dict[str, Any]]:
    p = _index_path(ws)
    mp = _meta_path(ws)
    if not p.exists() or not mp.exists() or np is None:
        return [], None, {}
    try:
        with np.load(p, allow_pickle=False) as data:
            vectors = data["vectors"]
        meta = json.loads(mp.read_text())
        return list(meta.get("node_ids") or []), vectors, meta
    except Exception:
        # Corrupt index — caller will rebuild from scratch on next update.
        return [], None, {}


def _save_index(
    ws: Workspace, node_ids: list[str], vectors: "np.ndarray", meta: dict[str, Any],
) -> None:
    if np is None:
        return
    np.savez_compressed(_index_path(ws), vectors=vectors)
    _meta_path(ws).write_text(json.dumps({**meta, "node_ids": node_ids}, indent=2))


# ---------------------------------------------------------------------------
# Node-text rendering
# ---------------------------------------------------------------------------

def _node_text(node_data: dict[str, Any]) -> str:
    """Compose the embedding input for a node. Label first, then short context
    (source file, community label) so semantically-near nodes from the same
    community + the same doc cluster slightly tighter.
    """
    label = (node_data.get("label") or "").strip()
    community = (node_data.get("community_label") or "").strip()
    source = (node_data.get("source_file") or "").strip()
    parts = [label]
    if community:
        parts.append(f"Community: {community}")
    if source:
        parts.append(f"Source: {source}")
    return "\n".join(parts)[:_MAX_NODE_TEXT_CHARS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_index(ws: Workspace, graph: Any) -> dict[str, Any]:
    """Compute embeddings for any nodes in `graph` not already in the index.

    If the saved index was built with a different provider/model/dim, rebuild
    from scratch — mixing models in one vector space is meaningless.
    Returns {added, total, model, skipped} for observability.
    """
    provider = _provider()
    if provider is None or np is None:
        return {"added": 0, "total": 0, "model": None, "skipped": True}

    current_ids: list[str] = []
    current_vecs: "np.ndarray | None" = None
    if hasattr(graph, "nodes"):
        current_ids = list(graph.nodes())  # NetworkX
    elif isinstance(graph, dict):
        current_ids = [n.get("id") for n in (graph.get("nodes") or []) if n.get("id")]

    saved_ids, saved_vecs, saved_meta = _load_index(ws)
    same_model = (
        saved_meta.get("model") == provider["model"]
        and saved_meta.get("dim") == provider["dim"]
    )

    if same_model and saved_vecs is not None:
        saved_set = set(saved_ids)
        new_ids = [nid for nid in current_ids if nid not in saved_set]
    else:
        # Model changed or no prior index — rebuild from scratch.
        new_ids = list(current_ids)
        saved_ids, saved_vecs = [], None

    if not new_ids:
        return {
            "added": 0,
            "total": len(saved_ids),
            "model": provider["model"],
            "skipped": False,
        }

    # Build the node-text list for new nodes in deterministic order.
    new_texts: list[str] = []
    for nid in new_ids:
        nd = graph.nodes[nid] if hasattr(graph, "nodes") else {}
        new_texts.append(_node_text(nd) or nid)

    vectors_new: list[list[float]] = []
    for i in range(0, len(new_texts), _BATCH_SIZE):
        batch = new_texts[i : i + _BATCH_SIZE]
        try:
            vectors_new.extend(provider["embed"](batch))
        except Exception as exc:
            # Don't poison the whole index. Log via meta and stop here.
            print(f"[embeddings] batch embed failed: {exc}", flush=True)
            break

    if not vectors_new:
        return {
            "added": 0,
            "total": len(saved_ids),
            "model": provider["model"],
            "skipped": False,
            "error": "embed failed",
        }

    new_arr = np.asarray(vectors_new, dtype=np.float32)
    # L2-normalize once so cosine == dot product at query time.
    norms = np.linalg.norm(new_arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    new_arr = new_arr / norms

    if saved_vecs is not None:
        all_vecs = np.concatenate([saved_vecs, new_arr], axis=0)
        # If we only embedded a prefix of new_ids (batch failure), trim to match.
        added_ids = new_ids[: len(vectors_new)]
        all_ids = saved_ids + added_ids
    else:
        all_vecs = new_arr
        all_ids = new_ids[: len(vectors_new)]

    meta = {
        "model": provider["model"],
        "kind": provider["kind"],
        "dim": provider["dim"],
        "created_at": saved_meta.get("created_at") or time.time(),
        "updated_at": time.time(),
        "size": len(all_ids),
    }
    _save_index(ws, all_ids, all_vecs, meta)
    return {
        "added": len(vectors_new),
        "total": len(all_ids),
        "model": provider["model"],
        "skipped": False,
    }


def top_k(ws: Workspace, query: str, k: int = 20) -> list[tuple[str, float]]:
    """Return [(node_id, cosine_similarity), …] for the top-k most relevant
    nodes. Empty list if the index is unavailable or empty.
    """
    provider = _provider()
    if provider is None or np is None or not query.strip():
        return []
    node_ids, vectors, meta = _load_index(ws)
    if vectors is None or not node_ids:
        return []
    if meta.get("model") != provider["model"]:
        # Model drift — cannot mix. Caller can rebuild via update_index().
        return []

    try:
        q_vecs = provider["embed"]([query[:_MAX_NODE_TEXT_CHARS]])
    except Exception as exc:
        print(f"[embeddings] query embed failed: {exc}", flush=True)
        return []

    q = np.asarray(q_vecs[0], dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    # Vectors are pre-normalized → dot product == cosine similarity.
    scores = vectors @ q
    if len(scores) <= k:
        idx = np.argsort(-scores)
    else:
        # argpartition is O(N); refine the top-k slice with argsort.
        part = np.argpartition(-scores, k)[:k]
        idx = part[np.argsort(-scores[part])]
    return [(node_ids[int(i)], float(scores[int(i)])) for i in idx]


def index_stats(ws: Workspace) -> dict[str, Any]:
    """Lightweight stats for the /api/stats endpoint or UI badges.

    `available` means an embedding provider is configured AND numpy is present.
    `built` reports whether an actual index file exists. The UI can use
    `available && !built` to surface a 'click to build' affordance.
    """
    provider_ready = _provider() is not None and np is not None
    node_ids, vectors, meta = _load_index(ws)
    built = vectors is not None
    out: dict[str, Any] = {
        "available": provider_ready,
        "built": built,
    }
    if built:
        out.update({
            "size": len(node_ids),
            "model": meta.get("model"),
            "dim": meta.get("dim"),
            "updated_at": meta.get("updated_at"),
        })
    return out
