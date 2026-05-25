"""FastAPI app: upload docs, rebuild graph, query, serve the React frontend."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Route all TLS verification through the OS trust store. Required on machines
# with a corporate MITM proxy (e.g. Netskope) whose CA cert lacks the keyUsage
# extension that Python 3.10+ now enforces.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

# Bootstrap workspaces (migrates legacy data into a 'default' workspace on first run).
import workspaces as ws_store  # noqa: E402
from workspaces import Workspace  # noqa: E402
import index_jobs  # noqa: E402

ws_store.ensure_default_workspace()

from graphify_runner import (  # noqa: E402  (load_dotenv must run first)
    _anthropic_client,
    delete_file,
    delete_repo,
    explain_node,
    find_path,
    full_graph_json,
    get_insights,
    graph_stats,
    copy_repo_files,
    ingest_repo,
    fetch_url_to_workspace,
    ingest_url,
    link_documents,
    list_repos,
    list_uploaded_files,
    query_graph,
    rebuild_graph,
    render_graph_context,
    rich_query,
    save_upload,
    synthesize_answer,
)
import conversations as conv_store  # noqa: E402
import rubrics as rubric_store  # noqa: E402
import memory as memory_store  # noqa: E402
import simulate as sim_store  # noqa: E402
import foresight  # noqa: E402
import artifacts  # noqa: E402
import playbooks  # noqa: E402
import intent_store  # noqa: E402
import playbook_store  # noqa: E402
import kb_corrections  # noqa: E402

rubric_store.seed_defaults()

# Any playbook run still marked running/queued from a previous process is
# orphaned (daemon threads die with the process). Sweep them on startup so
# the UI doesn't show an eternal spinner.
_swept = playbooks.cleanup_orphaned_runs()
if _swept:
    print(f"[startup] swept {_swept} orphaned playbook run(s) → status=failed")


def active_workspace(x_workspace_id: str | None = Header(default=None)) -> Workspace:
    """Resolve the active workspace from the X-Workspace-Id header.
    Falls back to the most-recently-updated workspace if no header is sent.
    """
    if x_workspace_id:
        ws = ws_store.get_workspace(x_workspace_id)
        if ws is None:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {x_workspace_id}")
        return ws
    listing = ws_store.list_workspaces()
    if not listing:
        raise HTTPException(status_code=500, detail="No workspaces exist")
    fallback = ws_store.get_workspace(listing[0]["id"])
    assert fallback is not None
    return fallback


def _workspace_default_rubric_body(ws: Workspace) -> str:
    """Return the body of the first/default rubric for the workspace, or empty
    string if no rubrics are configured. Used to auto-apply rubric framing to
    one-off LLM calls (Ask Graph, Explain Node) that don't have an explicit
    rubric setting in the UI."""
    try:
        rubrics = rubric_store.list_rubrics(ws)
        if not rubrics:
            return ""
        return (rubrics[0].get("body") or "").strip()
    except Exception:
        return ""


app = FastAPI(title="graphify web", version="0.1.0")

# Permissive CORS — the app is single-origin in production but the Vite dev server
# runs on a different port. Tighten this if you ever expose the API publicly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Schemas ----------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str
    mode: str = "bfs"
    synthesize: bool = True
    budget: int = 2000
    web_grounding: bool = False


class ExplainRequest(BaseModel):
    node: str
    web_grounding: bool = False


class WorkspaceCreateRequest(BaseModel):
    name: str
    source_workspace_id: str | None = None
    # Optional: pick existing rubrics to seed the new workspace. Each entry is
    # {workspace_id?: str|null, rubric_id: str}. workspace_id=None means a
    # built-in template; otherwise the rubric is copied from that workspace's
    # rubrics dir. Snapshots — edits don't propagate.
    seed_rubrics: list[dict] | None = None


class WorkspaceRenameRequest(BaseModel):
    name: str


class WebResearchRequest(BaseModel):
    query: str
    filename: str | None = None


class PlaybookRunRequest(BaseModel):
    playbook_id: str
    scenario: str
    horizon: str = "1y"
    source_artifact_id: str | None = None
    rubric_id: str | None = None
    web_grounding: bool = True
    synth_inference_strategy: str = "none"
    fact_check: bool = False
    answer_model: str | None = None


class PathRequest(BaseModel):
    source: str
    target: str


class IngestUrlRequest(BaseModel):
    url: str
    author: str | None = None
    contributor: str | None = None


class IngestRepoRequest(BaseModel):
    path: str
    name: str | None = None


class CreateConversationRequest(BaseModel):
    title: str
    intent: str | None = None
    rubric_id: str | None = None
    inference_strategy: str | None = "none"
    web_grounding: bool | None = False
    auto_memory: bool | None = False
    answer_model: str | None = None


class RenameConversationRequest(BaseModel):
    title: str


class ConversationSettingsRequest(BaseModel):
    intent: str | None = None
    rubric_id: str | None = None
    inference_strategy: str | None = None
    web_grounding: bool | None = None
    auto_memory: bool | None = None
    answer_model: str | None = None


class MemoryItemRequest(BaseModel):
    text: str
    tag: str | None = None


class MemoryUpdateRequest(BaseModel):
    text: str | None = None
    tag: str | None = None


class SimulateRequest(BaseModel):
    question: str
    horizon: str = "1y"  # one of "6mo", "1y", "3y"
    use_graph: bool = True
    web_grounding: bool = False
    use_memory: bool = True


# --- ForeSight request schemas ---------------------------------------------

class ForesightPersonaCreate(BaseModel):
    label: str
    tagline: str | None = ""
    system: str
    color: str | None = None


class ForesightPersonaUpdate(BaseModel):
    label: str | None = None
    tagline: str | None = None
    system: str | None = None
    color: str | None = None


class ForesightSessionCreate(BaseModel):
    title: str
    scenario: str
    horizon: str = "1y"
    persona_ids: list[str] = []
    rounds: int = 2
    world_context: str = ""
    rubric_id: str | None = None
    use_graph: bool = True
    answer_model: str | None = None
    source_conversation_id: str | None = None
    source_conversation_title: str | None = None
    use_memory: bool = True
    web_grounding: bool = False
    synth_inference_strategy: str = "none"


class ForesightSessionUpdate(BaseModel):
    title: str | None = None
    scenario: str | None = None
    horizon: str | None = None
    rounds: int | None = None
    world_context: str | None = None
    personas: list[str] | None = None
    rubric_id: str | None = None
    use_graph: bool | None = None
    answer_model: str | None = None
    source_conversation_id: str | None = None
    use_memory: bool | None = None
    web_grounding: bool | None = None
    synth_inference_strategy: str | None = None


class RubricCreateRequest(BaseModel):
    name: str
    body: str


class RubricUpdateRequest(BaseModel):
    name: str | None = None
    body: str | None = None


class TurnRequest(BaseModel):
    text: str


class PinRequest(BaseModel):
    kind: str  # "node" | "answer" | "note"
    label: str | None = None
    node_id: str | None = None
    text: str | None = None


# --- API routes -------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stats")
def stats(ws: Workspace = Depends(active_workspace)) -> dict:
    base = graph_stats(ws)
    try:
        import embeddings as _emb
        base["embeddings"] = _emb.index_stats(ws)
    except Exception:
        base["embeddings"] = {"available": False}
    base["indexing"] = index_jobs.get_job(ws.id) or {"status": "idle"}
    return base


@app.get("/api/index-job")
def index_job(ws: Workspace = Depends(active_workspace)) -> dict:
    """Current (or most recent) indexing job for the active workspace. Frontend
    polls this while a job is running to show a banner + completion toast."""
    job = index_jobs.get_job(ws.id)
    if job is None:
        return {"status": "idle"}
    return job


def _start_rebuild_job(ws: Workspace, *, kind: str, label: str) -> dict:
    """Helper: kick off rebuild_graph on a background thread. Returns the job
    descriptor immediately so the API handler doesn't block. Raises 409 if a
    job is already running for this workspace."""
    try:
        return index_jobs.start(
            ws.id, kind=kind, label=label,
            fn=rebuild_graph, args=(ws,),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/docs")
def docs(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"files": list_uploaded_files(ws), "repos": list_repos(ws)}


@app.post("/api/ingest-repo")
def ingest_repo_route(
    req: IngestRepoRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Copy the repo files synchronously (seconds), then enqueue the graph
    rebuild on a background thread. Returns immediately with the job
    descriptor so the client doesn't time out on huge repos."""
    if not req.path.strip():
        raise HTTPException(status_code=400, detail="path is required")
    copy_result = copy_repo_files(ws, req.path.strip(), name=req.name)
    if "error" in copy_result and copy_result.get("copied", 0) == 0:
        raise HTTPException(status_code=400, detail=copy_result["error"])
    job = _start_rebuild_job(
        ws, kind="ingest_repo",
        label=f"Indexing {copy_result.get('repo', 'repo')} ({copy_result.get('copied', 0)} files)",
    )
    return {**copy_result, "job": job}


@app.delete("/api/repos/{name}")
def delete_repo_route(name: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not delete_repo(ws, name):
        raise HTTPException(status_code=404, detail="repo not found")
    job = _start_rebuild_job(ws, kind="delete_repo", label=f"Re-indexing after removing {name}")
    return {"deleted": name, "job": job}


@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    ws: Workspace = Depends(active_workspace),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    saved: list[str] = []
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        if not data:
            continue
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds 50MB limit")
        path = save_upload(ws, f.filename, data)
        saved.append(path.name)
    job = _start_rebuild_job(
        ws, kind="upload",
        label=f"Indexing {len(saved)} new file{'s' if len(saved) != 1 else ''}",
    )
    return {"saved": saved, "job": job}


@app.delete("/api/docs/{filename}")
def delete_doc(filename: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not delete_file(ws, filename):
        raise HTTPException(status_code=404, detail="File not found")
    job = _start_rebuild_job(ws, kind="delete_doc", label=f"Re-indexing after removing {filename}")
    return {"deleted": filename, "job": job}


@app.post("/api/rebuild")
def rebuild(ws: Workspace = Depends(active_workspace)) -> dict:
    """Force a full graph rebuild for the active workspace on a background
    thread. Re-runs AST + LLM extraction, re-clusters, re-labels, refreshes
    embeddings. Use after prompts/models change or manual edits to data/raw.
    Returns the job descriptor; client polls /api/index-job for status."""
    job = _start_rebuild_job(ws, kind="rebuild", label="Rebuilding graph from scratch")
    return {"job": job}


@app.post("/api/query")
def query(req: QueryRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty")
    result = query_graph(ws, req.question, mode=req.mode, budget=req.budget)
    if req.synthesize and "rendered" in result:
        try:
            # Always apply the workspace's default rubric + the user's
            # persistent memory so this one-off Ask Graph call is framed the
            # same way as Conversations and Playbooks.
            rubric_body = _workspace_default_rubric_body(ws)
            memory_block = memory_store.memory_block(ws)
            answer_out = synthesize_answer(
                req.question,
                result["rendered"],
                web_grounding=req.web_grounding,
                rubric_body=rubric_body,
                memory_block=memory_block,
            )
            if answer_out:
                result["answer"] = answer_out.get("text", "")
                if answer_out.get("web_sources"):
                    result["web_sources"] = answer_out["web_sources"]
                if answer_out.get("gaps"):
                    result["gaps"] = answer_out["gaps"]
        except Exception as exc:  # noqa: BLE001 — we want the UI to keep working
            result["answer_error"] = str(exc)
    return result


@app.get("/api/insights")
def insights(ws: Workspace = Depends(active_workspace)) -> dict:
    return get_insights(ws)


@app.post("/api/insights/web-context")
def insights_web_context(ws: Workspace = Depends(active_workspace)) -> dict:
    """Pull recent web context for the top god-nodes + sharpen the suggested questions.

    Returns {"god_context": [{node, label, summary, web_sources}], "questions": [...]}.
    """
    client = _anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="LLM not configured")
    data = get_insights(ws)
    god_nodes_list = data.get("gods", [])[:5]
    suggested = data.get("questions", [])[:8]

    god_context = []
    seen_urls: set[str] = set()

    for g in god_nodes_list:
        label = g.get("label") or g.get("id") or ""
        if not label:
            continue
        try:
            msg = client.messages.create(
                model=os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=400,
                system=(
                    "You add a 2-3 sentence 'current world status' note for an entity, using "
                    "the web_search tool to ground recency. Cite domain. Be specific. If the "
                    "entity is generic/abstract and web search wouldn't help, say so briefly."
                ),
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "2")),
                }],
                messages=[{"role": "user", "content": f"Entity: {label}"}],
            )
            text_parts: list[str] = []
            local_sources: list[dict[str, str]] = []
            for b in msg.content:
                t = getattr(b, "type", None)
                if t == "text":
                    text_parts.append(b.text)
                elif t == "web_search_tool_result":
                    results = getattr(b, "content", None)
                    if isinstance(results, list):
                        for r in results:
                            url = getattr(r, "url", None) or ""
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                local_sources.append({"title": getattr(r, "title", None) or "", "url": url})
            god_context.append({
                "id": g.get("id"),
                "label": label,
                "summary": "".join(text_parts).strip(),
                "web_sources": local_sources,
            })
        except Exception as exc:  # noqa: BLE001
            god_context.append({"id": g.get("id"), "label": label, "summary": f"(failed: {exc})", "web_sources": []})

    return {"god_context": god_context, "suggested_questions": suggested}


@app.get("/api/communities")
def communities(ws: Workspace = Depends(active_workspace)) -> dict:
    """Return labeled communities with member node summaries."""
    insights = get_insights(ws)
    G_data = full_graph_json(ws)
    nodes_by_id = {n["id"]: n for n in G_data.get("nodes", [])}

    out = []
    labels = insights.get("community_labels", {})
    cohesion = insights.get("cohesion", {})
    for cid_str, members in insights.get("communities", {}).items():
        member_nodes = []
        for nid in members:
            n = nodes_by_id.get(nid)
            if n:
                member_nodes.append({
                    "id": nid,
                    "label": n.get("label", nid),
                    "source_file": n.get("source_file"),
                })
        out.append({
            "id": int(cid_str),
            "label": labels.get(cid_str, f"Community {cid_str}"),
            "size": len(members),
            "cohesion": cohesion.get(cid_str),
            "nodes": member_nodes,
        })
    out.sort(key=lambda c: c["size"], reverse=True)
    return {"communities": out}


@app.post("/api/explain")
def explain(req: ExplainRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.node.strip():
        raise HTTPException(status_code=400, detail="Node name is empty")
    return explain_node(
        ws, req.node,
        web_grounding=req.web_grounding,
        rubric_body=_workspace_default_rubric_body(ws),
        memory_block=memory_store.memory_block(ws),
    )


@app.post("/api/path")
def path(req: PathRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.source.strip() or not req.target.strip():
        raise HTTPException(status_code=400, detail="Both source and target required")
    return find_path(ws, req.source, req.target)


@app.post("/api/ingest-url")
def ingest_url_route(req: IngestUrlRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is empty")
    out = fetch_url_to_workspace(ws, req.url, author=req.author, contributor=req.contributor)
    if "error" in out:
        raise HTTPException(status_code=400, detail=out["error"])
    job = _start_rebuild_job(ws, kind="ingest_url", label=f"Indexing {out.get('saved', 'URL')}")
    return {**out, "job": job}


@app.post("/api/relink")
def relink(ws: Workspace = Depends(active_workspace)) -> dict:
    """Run the cross-document linker on the current graph (no re-extraction)."""
    return link_documents(ws)


# --- Conversations -----------------------------------------------------------

@app.get("/api/conversations")
def conversations_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"conversations": conv_store.list_conversations(ws)}


@app.post("/api/conversations")
def conversations_create(
    req: CreateConversationRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    return conv_store.create_conversation(
        ws,
        req.title,
        intent=req.intent,
        rubric_id=req.rubric_id,
        inference_strategy=req.inference_strategy or "none",
        web_grounding=bool(req.web_grounding),
        auto_memory=bool(req.auto_memory),
        answer_model=req.answer_model,
    )


@app.patch("/api/conversations/{conv_id}/settings")
def conversations_settings(
    conv_id: str, req: ConversationSettingsRequest,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    conv = conv_store.update_settings(
        ws,
        conv_id,
        intent=req.intent,
        rubric_id=req.rubric_id,
        inference_strategy=req.inference_strategy,
        web_grounding=req.web_grounding,
        auto_memory=req.auto_memory,
        answer_model=req.answer_model,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


# Models a user can pick for the synthesizer in Conversations. Kept in code so
# the UI doesn't drift from what the backend actually supports.
ANSWER_MODELS = [
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "hint": "fast & cheap"},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6", "hint": "balanced · default"},
    {"id": "claude-opus-4-7", "label": "Opus 4.7", "hint": "highest quality · slower"},
]


@app.get("/api/models")
def models_list() -> dict:
    default = os.environ.get("GRAPHIFY_ANSWER_MODEL", "claude-sonnet-4-6")
    return {"models": ANSWER_MODELS, "default": default}


# --- Memory -----------------------------------------------------------------

@app.get("/api/memory")
def memory_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"items": memory_store.list_items(ws)}


@app.post("/api/memory")
def memory_create(req: MemoryItemRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="empty memory text")
    return memory_store.add_item(ws, req.text, source="manual", tag=req.tag)


@app.patch("/api/memory/{mid}")
def memory_update(mid: str, req: MemoryUpdateRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    item = memory_store.update_item(ws, mid, text=req.text, tag=req.tag)
    if not item:
        raise HTTPException(status_code=404, detail="memory item not found")
    return item


@app.delete("/api/memory/{mid}")
def memory_delete(mid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not memory_store.delete_item(ws, mid):
        raise HTTPException(status_code=404, detail="memory item not found")
    return {"deleted": mid}


@app.get("/api/intents")
def intents(ws: Workspace = Depends(active_workspace)) -> dict:
    """All intents visible from this workspace — built-ins + user-defined,
    grouped for the UI. Built-ins with a user record at the same id show up
    as `source: customized` (only once)."""
    user_intents = intent_store.list_intents(ws)
    user_by_id = {ui["id"]: ui for ui in user_intents}
    builtin_ids = set(rubric_store.INTENT_LABELS.keys())
    groups: list[dict[str, Any]] = []
    for grp in rubric_store.INTENT_GROUPS:
        items: list[dict[str, Any]] = []
        for iid, label in grp["intents"].items():
            override = user_by_id.get(iid)
            if override:
                items.append({"id": iid, "label": override["label"], "source": "customized"})
            else:
                items.append({"id": iid, "label": label, "source": "builtin"})
        groups.append({"label": grp["label"], "intents": items})
    # Append user-only intents (those NOT shadowing a built-in id).
    group_index = {g["label"]: g for g in groups}
    for ui in user_intents:
        if ui["id"] in builtin_ids:
            continue
        target_name = ui.get("group") or "Custom"
        if target_name not in group_index:
            new_group = {"label": target_name, "intents": []}
            groups.append(new_group)
            group_index[target_name] = new_group
        group_index[target_name]["intents"].append({
            "id": ui["id"],
            "label": ui["label"],
            "source": ui.get("scope", "workspace"),
        })
    # Backwards-compatible flat map (override labels win).
    flat: dict[str, str] = dict(rubric_store.INTENT_LABELS)
    for ui in user_intents:
        flat[ui["id"]] = ui["label"]
    return {"intents": flat, "groups": groups}


@app.get("/api/intents/custom")
def intents_custom_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"intents": intent_store.list_intents(ws)}


@app.get("/api/intents/source/{iid}")
def intents_source_get(iid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    """Return the full prompt body for any intent — built-in, override, or user.

    Override (user record with same id as a built-in) wins. Source distinguishes
    built-in (no override), customized (built-in id with override), or scope
    of a user-only intent (workspace/global).
    """
    user_intent = intent_store.get_intent(ws, iid)
    if iid in rubric_store.INTENT_LABELS:
        group_label = ""
        for grp in rubric_store.INTENT_GROUPS:
            if iid in grp["intents"]:
                group_label = grp["label"]
                break
        if user_intent:
            return {
                "id": iid,
                "label": user_intent["label"],
                "group": user_intent.get("group") or group_label,
                "body": user_intent["body"],
                "source": "customized",
            }
        return {
            "id": iid,
            "label": rubric_store.INTENT_LABELS[iid],
            "group": group_label,
            "body": rubric_store.intent_instruction(iid, ws),
            "source": "builtin",
        }
    if not user_intent:
        raise HTTPException(status_code=404, detail="intent not found")
    return {
        "id": user_intent["id"],
        "label": user_intent["label"],
        "group": user_intent["group"],
        "body": user_intent["body"],
        "source": user_intent.get("scope", "workspace"),
    }


class IntentCreateRequest(BaseModel):
    id: str
    group: str = "Custom"
    label: str
    body: str
    scope: str = "workspace"


class IntentUpdateRequest(BaseModel):
    group: str | None = None
    label: str | None = None
    body: str | None = None


@app.post("/api/intents/custom")
def intents_custom_create(req: IntentCreateRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        return intent_store.create_intent(
            ws, iid=req.id, group=req.group, label=req.label,
            body=req.body, scope=req.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/intents/custom/{iid}")
def intents_custom_update(iid: str, req: IntentUpdateRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    # If iid matches a built-in and no override exists yet, update_intent
    # materializes one in workspace scope; otherwise it patches in-place.
    try:
        updated = intent_store.update_intent(
            ws, iid, group=req.group, label=req.label, body=req.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="intent not found")
    return updated


@app.delete("/api/intents/custom/{iid}")
def intents_custom_delete(iid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not intent_store.delete_intent(ws, iid):
        raise HTTPException(status_code=404, detail="intent not found")
    return {"deleted": iid}


@app.post("/api/intents/{iid}/restore-default")
def intents_restore(iid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if iid not in rubric_store.INTENT_LABELS:
        raise HTTPException(status_code=400, detail="not a built-in intent")
    intent_store.restore_builtin_intent(ws, iid)
    # Return the canonical view (no override) so the UI can re-render in place.
    group_label = ""
    for grp in rubric_store.INTENT_GROUPS:
        if iid in grp["intents"]:
            group_label = grp["label"]
            break
    return {
        "id": iid,
        "label": rubric_store.INTENT_LABELS[iid],
        "group": group_label,
        "body": rubric_store.intent_instruction(iid, ws),
        "source": "builtin",
    }


class IntentCloneRequest(BaseModel):
    new_id: str | None = None
    scope: str = "workspace"


@app.post("/api/intents/{iid}/clone")
def intents_clone(iid: str, req: IntentCloneRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        return intent_store.clone_intent(ws, iid, new_id=req.new_id, scope=req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/rubrics")
def rubrics_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"rubrics": rubric_store.list_rubrics(ws)}


@app.get("/api/rubrics/available")
def rubrics_available() -> dict:
    """List every rubric a user could copy into a new workspace — built-in
    templates + each rubric across every existing workspace. Used by the
    create-workspace UI's rubric picker (no `active workspace` needed)."""
    return {"rubrics": rubric_store.list_available_rubrics_for_picker()}


@app.post("/api/rubrics")
def rubrics_create(req: RubricCreateRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    return rubric_store.create_rubric(ws, req.name, req.body)


@app.patch("/api/rubrics/{rid}")
def rubrics_update(rid: str, req: RubricUpdateRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    r = rubric_store.update_rubric(ws, rid, name=req.name, body=req.body)
    if not r:
        raise HTTPException(status_code=404, detail="rubric not found")
    return r


@app.delete("/api/rubrics/{rid}")
def rubrics_delete(rid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    # Built-ins can't be deleted — only their overrides can. Surface that
    # explicitly so the UI doesn't show a misleading 404.
    if rid in rubric_store.DEFAULT_RUBRICS and not rubric_store._path(ws, rid).exists():
        raise HTTPException(status_code=400, detail="built-in rubrics cannot be deleted; use restore-default instead")
    if not rubric_store.delete_rubric(ws, rid):
        raise HTTPException(status_code=404, detail="rubric not found")
    return {"deleted": rid}


@app.post("/api/rubrics/{rid}/restore-default")
def rubrics_restore(rid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    r = rubric_store.restore_default_rubric(ws, rid)
    if not r:
        raise HTTPException(status_code=404, detail="not a built-in rubric")
    return r


# --- KB corrections / refinements ------------------------------------------

class KBCorrectionRequest(BaseModel):
    kind: str = "correction"
    target_node_id: str | None = None
    target_edge_id: str | None = None
    source_type: str = "human"
    author: str = ""
    author_basis: str = ""
    confidence: str = "medium"
    original_summary: str = ""
    new_summary: str = ""
    reason: str = ""
    evidence_url: str | None = None


class KBCorrectionUpdate(BaseModel):
    kind: str | None = None
    target_node_id: str | None = None
    target_edge_id: str | None = None
    source_type: str | None = None
    author: str | None = None
    author_basis: str | None = None
    confidence: str | None = None
    original_summary: str | None = None
    new_summary: str | None = None
    reason: str | None = None
    evidence_url: str | None = None


@app.get("/api/kb/corrections")
def kb_corrections_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"corrections": kb_corrections.list_corrections(ws)}


@app.post("/api/kb/corrections")
def kb_corrections_create(req: KBCorrectionRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        return kb_corrections.create_correction(ws, req.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/kb/corrections/{cid}")
def kb_corrections_update(cid: str, req: KBCorrectionUpdate, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        updated = kb_corrections.update_correction(ws, cid, req.dict(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="correction not found")
    return updated


@app.delete("/api/kb/corrections/{cid}")
def kb_corrections_delete(cid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not kb_corrections.delete_correction(ws, cid):
        raise HTTPException(status_code=404, detail="correction not found")
    return {"deleted": cid}


@app.get("/api/kb/diff")
def kb_diff(ws: Workspace = Depends(active_workspace)) -> dict:
    """Return the most recent re-ingestion diff (added / removed / relabeled
    nodes) so the Refine KB tab can surface what changed since the last
    rebuild. Empty if no diff has been computed yet."""
    path = ws.path / "kb_diff.json"
    if not path.exists():
        return {"diff": None}
    try:
        return {"diff": json.loads(path.read_text())}
    except Exception:
        return {"diff": None}


@app.get("/api/kb/nodes/search")
def kb_nodes_search(
    q: str = "",
    limit: int = 30,
    offset: int = 0,
    community: str | None = None,
    source_file: str | None = None,
    entity_type: str | None = None,
    include_code: bool = False,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    """Search / browse graph nodes. Used both by the composer's node picker
    (limit ~25, q-filtered) and by the Refine KB "Facts in the KB" view
    (paginated, community-filtered).

    `include_code=False` (default) filters out file_type=='code' nodes —
    code-derived facts (functions, classes) don't fit the fix/add/confirm/
    doubt gesture, so we hide them unless the caller explicitly asks. The
    UI exposes a "Show code nodes" toggle for the rare case of annotation.
    """
    if not ws.graph_file.exists():
        return {"results": [], "total": 0, "communities": [], "source_files": []}
    try:
        with ws.graph_file.open() as fp:
            data = json.load(fp)
    except Exception:
        return {"results": [], "total": 0, "communities": [], "source_files": []}
    nodes = data.get("nodes") or []
    links = data.get("links") or []
    needle = q.strip().lower()
    com_filter = (community or "").strip()
    src_filter = (source_file or "").strip()

    # Pre-index refinement counts so we don't re-walk the corrections store
    # per node.
    corrs_by_target: dict[str, dict[str, int]] = {}
    try:
        for c in kb_corrections.list_corrections(ws):
            tid = c.get("target_node_id")
            if tid:
                bucket = corrs_by_target.setdefault(tid, {})
                bucket[c["kind"]] = bucket.get(c["kind"], 0) + 1
    except Exception:
        corrs_by_target = {}

    # Node degree drives the no-query relevance ranking — high-degree nodes
    # are the load-bearing ones the LLM is most likely to surface, so they
    # come first when the user is browsing.
    degree: dict[str, int] = {}
    for lk in links:
        s = lk.get("source")
        t = lk.get("target")
        if s:
            degree[s] = degree.get(s, 0) + 1
        if t:
            degree[t] = degree.get(t, 0) + 1

    # Distinct community labels + source files + entity types for filter UI.
    communities_seen: dict[str, int] = {}
    sources_seen: dict[str, int] = {}
    entity_types_seen: dict[str, int] = {}
    et_filter = (entity_type or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for n in nodes:
        label = (n.get("label") or n.get("id") or "")
        com = n.get("community_label") or ""
        src = n.get("source_file") or ""
        et = (n.get("entity_type") or "").lower()
        if com:
            communities_seen[com] = communities_seen.get(com, 0) + 1
        if src:
            sources_seen[src] = sources_seen.get(src, 0) + 1
        if et:
            entity_types_seen[et] = entity_types_seen.get(et, 0) + 1
        if needle and needle not in str(label).lower():
            continue
        if com_filter and com != com_filter:
            continue
        if src_filter and src != src_filter:
            continue
        if et_filter and et != et_filter:
            continue
        # Code-derived facts (functions, classes, modules) don't fit the
        # refinement gesture — hide them by default. The UI exposes an
        # override for users who want to annotate code nodes anyway.
        if not include_code and (n.get("file_type") == "code"):
            continue
        filtered.append(n)

    # Rank by relevance before paginating, so the "top of the list" is the
    # most useful row regardless of where it sits in the underlying graph.
    # Score components (higher = more relevant):
    #   - Search match quality (only when q is set): exact > prefix > contains
    #   - Has any user refinement (correction/attestation/dissent): +50
    #     — humans have already paid attention to this fact, surface it again
    #   - Has a community label: +5
    #     — labeled communities are coherent, anchor nodes within them rank up
    #   - Node degree (number of edges): scaled down 10x to act as a tie-breaker
    #     — load-bearing nodes naturally come first
    def _score(n: dict[str, Any]) -> tuple[int, float, str]:
        nid = n.get("id") or ""
        label = str(n.get("label") or nid).lower()
        s = 0
        if needle:
            if label == needle:
                s += 1000
            elif label.startswith(needle):
                s += 500
            else:
                s += 100  # contains-match — already filtered above
        if corrs_by_target.get(nid):
            s += 50
        if n.get("community_label"):
            s += 5
        deg = degree.get(nid, 0)
        s += min(deg, 100) // 2  # cap so a hub doesn't drown out refinements
        # Tie-break: degree (full, not capped), then label alpha for stability.
        return (-s, -deg, label)

    filtered.sort(key=_score)

    total = len(filtered)
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    page = filtered[safe_offset : safe_offset + safe_limit]
    results = []
    for n in page:
        nid = n.get("id")
        results.append({
            "id": nid,
            "label": (n.get("label") or nid),
            "source_file": n.get("source_file"),
            "community_label": n.get("community_label"),
            "entity_type": n.get("entity_type"),
            "extracted_at": n.get("extracted_at"),
            "refinement_counts": corrs_by_target.get(nid, {}),
        })
    return {
        "results": results,
        "total": total,
        "communities": sorted(
            ({"label": k, "count": v} for k, v in communities_seen.items()),
            key=lambda r: -r["count"],
        ),
        "source_files": sorted(
            ({"label": k, "count": v} for k, v in sources_seen.items()),
            key=lambda r: -r["count"],
        ),
        "entity_types": sorted(
            ({"label": k, "count": v} for k, v in entity_types_seen.items()),
            key=lambda r: -r["count"],
        ),
    }


# --- Typed-entity relations layer ------------------------------------------
#
# Surfaces the deterministic entity-typing + role-edge pass that runs
# after Claude extraction (entity_extract.py). Two queries:
#
#   GET /api/entities                      — list nodes filtered by entity_type
#   GET /api/entities/relations            — list role-typed edges, filterable
#                                            by relation + subject + target
#
# Vector search can't answer "who works at Acme?" — these endpoints can,
# because the graph carries the typed edges deterministically extracted at
# rebuild time.

@app.get("/api/entities")
def entities_list(
    entity_type: str | None = None,
    q: str = "",
    limit: int = 100,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    """List nodes that the deterministic pass classified as typed
    entities (person / company / organization / product). Optionally
    filter by type or by a label substring."""
    if not ws.graph_file.exists():
        return {"results": [], "total": 0, "type_counts": {}}
    try:
        with ws.graph_file.open() as fp:
            data = json.load(fp)
    except Exception:
        return {"results": [], "total": 0, "type_counts": {}}

    needle = q.strip().lower()
    et_filter = (entity_type or "").strip().lower()
    type_counts: dict[str, int] = {}
    matched: list[dict[str, Any]] = []
    for n in data.get("nodes") or []:
        et = (n.get("entity_type") or "").lower()
        if not et:
            continue
        type_counts[et] = type_counts.get(et, 0) + 1
        if et_filter and et != et_filter:
            continue
        label = (n.get("label") or n.get("id") or "").lower()
        if needle and needle not in label:
            continue
        matched.append({
            "id": n.get("id"),
            "label": n.get("label"),
            "entity_type": n.get("entity_type"),
            "source_file": n.get("source_file"),
            "community_label": n.get("community_label"),
        })

    matched.sort(key=lambda r: (r["entity_type"] or "", (r["label"] or "").lower()))
    safe_limit = max(1, min(int(limit), 500))
    return {
        "results": matched[:safe_limit],
        "total": len(matched),
        "type_counts": type_counts,
    }


@app.get("/api/entities/relations")
def entities_relations(
    relation: str | None = None,
    subject_label: str | None = None,
    target_label: str | None = None,
    limit: int = 100,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    """List role-typed edges (works_at / founded / invested_in / attended
    / advises). Each filter is a substring match against the matching
    node's label (case-insensitive). Examples:

      /api/entities/relations?relation=works_at&target_label=Acme
        → "who works at Acme?"
      /api/entities/relations?relation=invested_in&subject_label=Bob
        → "what did Bob invest in?"
    """
    from entity_extract import ROLE_RELATIONS

    if not ws.graph_file.exists():
        return {"results": [], "total": 0, "relations": sorted(ROLE_RELATIONS)}
    try:
        with ws.graph_file.open() as fp:
            data = json.load(fp)
    except Exception:
        return {"results": [], "total": 0, "relations": sorted(ROLE_RELATIONS)}

    by_id: dict[str, dict[str, Any]] = {n.get("id"): n for n in (data.get("nodes") or []) if n.get("id")}
    rel_filter = (relation or "").strip().lower()
    subj_needle = (subject_label or "").strip().lower()
    tgt_needle = (target_label or "").strip().lower()

    matched: list[dict[str, Any]] = []
    for e in data.get("links") or []:
        r = (e.get("relation") or "").lower()
        if r not in ROLE_RELATIONS:
            continue
        if rel_filter and r != rel_filter:
            continue
        subj = by_id.get(e.get("source"))
        tgt = by_id.get(e.get("target"))
        if not subj or not tgt:
            continue
        subj_label = (subj.get("label") or "").lower()
        tgt_label = (tgt.get("label") or "").lower()
        if subj_needle and subj_needle not in subj_label:
            continue
        if tgt_needle and tgt_needle not in tgt_label:
            continue
        matched.append({
            "relation": r,
            "subject": {
                "id": subj.get("id"),
                "label": subj.get("label"),
                "entity_type": subj.get("entity_type"),
            },
            "target": {
                "id": tgt.get("id"),
                "label": tgt.get("label"),
                "entity_type": tgt.get("entity_type"),
            },
            "source_file": e.get("source_file"),
            "confidence": e.get("confidence"),
            "extractor": e.get("extractor"),
        })

    safe_limit = max(1, min(int(limit), 500))
    return {
        "results": matched[:safe_limit],
        "total": len(matched),
        "relations": sorted(ROLE_RELATIONS),
    }


@app.get("/api/conversations/{conv_id}")
def conversations_get(conv_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    c = conv_store.get_conversation(ws, conv_id)
    if not c:
        raise HTTPException(status_code=404, detail="conversation not found")
    return c


@app.delete("/api/conversations/{conv_id}")
def conversations_delete(conv_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not conv_store.delete_conversation(ws, conv_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"deleted": conv_id}


@app.patch("/api/conversations/{conv_id}")
def conversations_rename(
    conv_id: str, req: RenameConversationRequest,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    c = conv_store.rename_conversation(ws, conv_id, req.title)
    if not c:
        raise HTTPException(status_code=404, detail="conversation not found")
    return c


@app.post("/api/conversations/{conv_id}/turn")
def conversations_turn(
    conv_id: str, req: TurnRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Add a user turn, run rich_query, append the assistant turn, return updated conversation."""
    conv = conv_store.get_conversation(ws, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="empty message")

    # User turn first.
    conv = conv_store.add_turn(ws, conv_id, {"role": "user", "text": req.text.strip()})
    assert conv is not None  # noqa: S101 — guaranteed by the check above

    # Build history text from prior turns (excluding the just-added user turn).
    history_for_prompt = conv_store.conversation_history_text(
        {"turns": conv["turns"][:-1]}, max_turns=8
    )

    # Resolve intent + rubric + memory for this conversation.
    intent_text = rubric_store.intent_instruction(conv.get("intent"), ws)
    rubric_body = ""
    if conv.get("rubric_id"):
        r = rubric_store.get_rubric(ws, conv["rubric_id"])
        if r:
            rubric_body = r.get("body", "")
    mem_block = memory_store.memory_block(ws)

    result = rich_query(
        ws,
        req.text.strip(),
        history_text=history_for_prompt,
        intent_instruction=intent_text,
        rubric_body=rubric_body,
        memory_block=mem_block,
        inference_strategy=conv.get("inference_strategy", "none"),
        web_grounding=bool(conv.get("web_grounding", False)),
        answer_model=conv.get("answer_model"),
    )

    assistant_turn = {
        "role": "assistant",
        "text": result.get("answer", "(no answer)"),
        "grounded": result.get("grounded", False),
        "entry_node_ids": result["router"].get("entry_node_ids", []),
        "entry_node_labels": result["router"].get("entry_node_labels", []),
        "router_reasoning": result["router"].get("reasoning", ""),
        "needs_graph": result["router"].get("needs_graph", False),
        "subgraph_node_count": len(result.get("subgraph", {}).get("nodes", []) if result.get("subgraph") else []),
        "subgraph": result.get("subgraph"),
        "inference_strategy": result.get("inference", {}).get("strategy", "none"),
        "inference_steps": result.get("inference", {}).get("steps", []),
        "web_sources": result.get("web_sources", []),
        "gaps": result.get("gaps", []),
        "memory_used": bool(mem_block),
    }
    conv = conv_store.add_turn(ws, conv_id, assistant_turn)

    # Auto-extract memory if enabled on this conversation.
    if conv and conv.get("auto_memory"):
        try:
            from graphify_runner import _anthropic_client
            import os as _os
            client = _anthropic_client()
            if client:
                candidates = memory_store.auto_extract_candidates(
                    ws,
                    client,
                    _os.environ.get("GRAPHIFY_MEMORY_MODEL", "claude-haiku-4-5-20251001"),
                    req.text.strip(),
                    assistant_turn["text"],
                )
                for c in candidates:
                    memory_store.add_item(ws, c, source="auto")
        except Exception:
            pass

    return conv  # type: ignore[return-value]


@app.post("/api/conversations/{conv_id}/pin")
def conversations_pin(
    conv_id: str, req: PinRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    conv = conv_store.add_pin(ws, conv_id, {
        "kind": req.kind,
        "label": req.label,
        "node_id": req.node_id,
        "text": req.text,
    })
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@app.delete("/api/conversations/{conv_id}/pin/{pin_id}")
def conversations_unpin(
    conv_id: str, pin_id: str, ws: Workspace = Depends(active_workspace),
) -> dict:
    conv = conv_store.remove_pin(ws, conv_id, pin_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation or pin not found")
    return conv


@app.post("/api/conversations/{conv_id}/synthesize-scenarios")
def conversations_synthesize_scenarios(
    conv_id: str, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Distill the conversation into up to 3 candidate ForeSight scenarios.
    Uses the conversation's configured `answer_model`. Returns [] on any
    failure (no API key, no turns, LLM error) — the UI falls back silently."""
    return {"scenarios": conv_store.synthesize_scenarios(ws, conv_id)}


# --- ForeSight routes -------------------------------------------------------

@app.get("/api/foresight/personas")
def foresight_personas_list() -> dict:
    return {"personas": foresight.list_personas()}


@app.post("/api/foresight/personas")
def foresight_personas_create(req: ForesightPersonaCreate) -> dict:
    if not req.label.strip() or not req.system.strip():
        raise HTTPException(status_code=400, detail="label and system are required")
    return foresight.create_custom_persona(req.label, req.tagline or "", req.system, req.color)


@app.patch("/api/foresight/personas/{pid}")
def foresight_personas_update(pid: str, req: ForesightPersonaUpdate) -> dict:
    p = foresight.update_custom_persona(
        pid, label=req.label, tagline=req.tagline,
        system=req.system, color=req.color,
    )
    if not p:
        raise HTTPException(status_code=404, detail="persona not found")
    return p


@app.delete("/api/foresight/personas/{pid}")
def foresight_personas_delete(pid: str) -> dict:
    # For preset ids, this only removes the override and restores the preset.
    if not foresight.delete_custom_persona(pid):
        raise HTTPException(status_code=404, detail="nothing to delete (preset with no override)")
    return {"deleted": pid}


@app.post("/api/foresight/personas/{pid}/restore-default")
def foresight_personas_restore(pid: str) -> dict:
    p = foresight.restore_preset_persona(pid)
    if not p:
        raise HTTPException(status_code=404, detail="not a preset persona")
    return p


@app.get("/api/foresight/horizons")
def foresight_horizons() -> dict:
    return {"horizons": foresight.HORIZONS}


@app.get("/api/foresight/sessions")
def foresight_sessions_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"sessions": foresight.list_sessions(ws)}


@app.post("/api/foresight/sessions")
def foresight_sessions_create(
    req: ForesightSessionCreate, ws: Workspace = Depends(active_workspace),
) -> dict:
    if not req.scenario.strip():
        raise HTTPException(status_code=400, detail="scenario is required")
    if not req.persona_ids:
        raise HTTPException(status_code=400, detail="at least one persona is required")
    return foresight.create_session(
        ws,
        title=req.title, scenario=req.scenario, horizon=req.horizon,
        persona_ids=req.persona_ids, rounds=req.rounds,
        world_context=req.world_context, rubric_id=req.rubric_id,
        use_graph=req.use_graph, answer_model=req.answer_model,
        source_conversation_id=req.source_conversation_id,
        source_conversation_title=req.source_conversation_title,
        use_memory=req.use_memory,
        web_grounding=req.web_grounding,
        synth_inference_strategy=req.synth_inference_strategy,
    )


@app.get("/api/foresight/sessions/{sid}")
def foresight_sessions_get(sid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    s = foresight.get_session(ws, sid)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.patch("/api/foresight/sessions/{sid}")
def foresight_sessions_update(
    sid: str, req: ForesightSessionUpdate, ws: Workspace = Depends(active_workspace),
) -> dict:
    s = foresight.update_session(
        ws, sid, **{k: v for k, v in req.dict().items() if v is not None}
    )
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.delete("/api/foresight/sessions/{sid}")
def foresight_sessions_delete(sid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not foresight.delete_session(ws, sid):
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": sid}


@app.post("/api/foresight/sessions/{sid}/run")
def foresight_sessions_run(sid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    session = foresight.get_session(ws, sid)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    client = _anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    rubric_body = ""
    if session.get("rubric_id"):
        r = rubric_store.get_rubric(ws, session["rubric_id"])
        if r:
            rubric_body = r.get("body", "")

    # Persistent memory (always loaded; effective only if session.use_memory is true).
    mem_block = memory_store.memory_block(ws)

    # If the session is linked to a conversation, render the last few turns as context.
    # Also borrow the conversation's intent (style preset) for the synthesizer.
    conversation_history = ""
    intent_text = ""
    src_id = session.get("source_conversation_id")
    if src_id:
        src_conv = conv_store.get_conversation(ws, src_id)
        if src_conv:
            conversation_history = conv_store.conversation_history_text(src_conv, max_turns=8)
            intent_text = rubric_store.intent_instruction(src_conv.get("intent"), ws)

    def graph_ctx(question: str) -> dict:
        try:
            return render_graph_context(ws, question)
        except Exception:
            return {"rendered": "", "entry_node_labels": []}

    return foresight.run_session(
        ws, sid, client,
        graph_context_fn=graph_ctx,
        rubric_body=rubric_body,
        memory_block=mem_block,
        conversation_history=conversation_history,
        intent_instruction=intent_text,
    )


@app.get("/api/simulate/personas")
def simulate_personas() -> dict:
    return {
        "personas": [
            {"key": k, "label": v["label"], "tagline": v["tagline"]}
            for k, v in sim_store.PERSONAS.items()
        ],
        "horizons": sim_store.HORIZONS,
    }


@app.post("/api/conversations/{conv_id}/simulate")
def conversations_simulate(
    conv_id: str, req: SimulateRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Run a multi-agent scenario simulation; append as a special turn."""
    conv = conv_store.get_conversation(ws, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="empty question")

    client = _anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="LLM not configured (ANTHROPIC_API_KEY missing)")

    history_for_prompt = conv_store.conversation_history_text(conv, max_turns=6)
    rubric_body = ""
    if conv.get("rubric_id"):
        r = rubric_store.get_rubric(ws, conv["rubric_id"])
        if r:
            rubric_body = r.get("body", "")
    mem_block = memory_store.memory_block(ws) if req.use_memory else ""
    intent_text = rubric_store.intent_instruction(conv.get("intent"), ws)
    inference_strategy = conv.get("inference_strategy", "none") or "none"

    graph_ctx = {"rendered": "", "entry_node_labels": []}
    if req.use_graph:
        try:
            graph_ctx = render_graph_context(ws, req.question.strip(), history_for_prompt)
        except Exception:
            graph_ctx = {"rendered": "", "entry_node_labels": []}

    result = sim_store.run_simulation(
        client,
        req.question.strip(),
        req.horizon,
        graph_context=graph_ctx.get("rendered", ""),
        history_text=history_for_prompt,
        rubric_body=rubric_body,
        memory_block=mem_block,
        web_grounding=req.web_grounding,
        intent_instruction=intent_text,
        inference_strategy=inference_strategy,
    )
    result["entry_node_labels"] = graph_ctx.get("entry_node_labels", [])

    turn = {
        "role": "simulation",
        "text": result["synthesis"],  # so transcripts/exports include the synthesis
        "simulation": result,
    }
    conv = conv_store.add_turn(ws, conv_id, turn)
    return conv  # type: ignore[return-value]


@app.post("/api/conversations/{conv_id}/export")
def conversations_export(conv_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    """Generate an executive-friendly Markdown report from the conversation."""
    conv = conv_store.get_conversation(ws, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"markdown": _export_executive_markdown(conv)}


def _export_executive_markdown(conv: dict) -> str:
    """Compose an exec-style report.

    We let the LLM do a final pass over the full transcript with an exec-summary
    prompt, so the output is tight prose rather than a raw chat log.
    """
    from graphify_runner import _anthropic_client  # local import to avoid circular issues
    import os as _os

    client = _anthropic_client()
    transcript_lines = []
    for t in conv.get("turns", []):
        role = t.get("role", "user").upper()
        transcript_lines.append(f"### {role}\n{t.get('text', '').strip()}")
    transcript = "\n\n".join(transcript_lines)

    pins_lines = []
    for p in conv.get("pins", []):
        if p.get("kind") == "node" and p.get("label"):
            pins_lines.append(f"- **Node:** {p['label']}")
        elif p.get("text"):
            pins_lines.append(f"- **{p.get('kind', 'note').title()}:** {p['text']}")
    pins_block = "\n\nPinned items:\n" + "\n".join(pins_lines) if pins_lines else ""

    intent = conv.get("intent") or "explore"
    intent_label = rubric_store.INTENT_LABELS.get(intent, intent)

    if not client or not transcript.strip():
        # Fallback: dump transcript verbatim with a header.
        return f"# {conv.get('title', 'Conversation')}\n\n**Intent:** {intent_label}\n\n{transcript}{pins_block}"

    prompt = (
        f"Write an executive-friendly Markdown report from the conversation transcript below.\n\n"
        f"Title: {conv.get('title', 'Conversation')}\n"
        f"Conversation intent: {intent_label}\n\n"
        f"Structure: 1) one-paragraph TL;DR, 2) Key findings as a bulleted list (each finding "
        f"with its source citation if present in transcript), 3) Recommended next steps (3-5 "
        f"bullets), 4) Open questions (if any). No filler. ~400-600 words total. Pure Markdown.\n\n"
        f"Transcript:\n{transcript}{pins_block}"
    )
    try:
        msg = client.messages.create(
            model=_os.environ.get("GRAPHIFY_EXPORT_MODEL", "claude-sonnet-4-6"),
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return f"# {conv.get('title', 'Conversation')}\n\n(export failed: {exc})\n\n{transcript}"

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return text or f"# {conv.get('title', 'Conversation')}\n\n{transcript}{pins_block}"


@app.get("/api/graph")
def graph(ws: Workspace = Depends(active_workspace)) -> dict:
    return full_graph_json(ws)


# --- Workspaces -------------------------------------------------------------

@app.get("/api/workspaces")
def workspaces_list() -> dict:
    return {"workspaces": ws_store.list_workspaces()}


@app.post("/api/workspaces")
def workspaces_create(req: WorkspaceCreateRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="name is required")

    # Resolve seed_rubrics (picker entries → full snapshots to write into the
    # new workspace's rubrics dir). Tolerates entries missing required fields
    # by skipping them rather than failing the whole create.
    seed_snapshots: list[dict] = []
    for entry in (req.seed_rubrics or []):
        if not isinstance(entry, dict):
            continue
        rid = entry.get("rubric_id") or entry.get("id")
        if not rid:
            continue
        ws_id = entry.get("workspace_id")
        snap: dict | None = None
        if ws_id:
            src_ws = ws_store.get_workspace(ws_id)
            if src_ws is not None:
                snap = rubric_store.get_rubric(src_ws, rid)
        if snap is None:
            # Fall back to the built-in registry (workspace_id was null or the
            # source workspace didn't have a stored override).
            snap = rubric_store.DEFAULT_RUBRICS.get(rid)
            if snap is not None:
                snap = {**snap}  # don't mutate the in-code template
        if not snap:
            continue
        seed_snapshots.append({
            "id": snap.get("id") or rid,
            "name": snap.get("name") or "Untitled rubric",
            "body": snap.get("body") or "",
        })

    try:
        ws = ws_store.create_workspace(
            req.name,
            source_workspace_id=req.source_workspace_id,
            seed_rubrics=seed_snapshots or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ws.summary()


@app.get("/api/workspaces/{ws_id}")
def workspaces_get(ws_id: str) -> dict:
    ws = ws_store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="workspace not found")
    return ws.summary()


@app.patch("/api/workspaces/{ws_id}")
def workspaces_rename(ws_id: str, req: WorkspaceRenameRequest) -> dict:
    ws = ws_store.rename_workspace(ws_id, req.name)
    if not ws:
        raise HTTPException(status_code=404, detail="workspace not found")
    return ws.summary()


@app.delete("/api/workspaces/{ws_id}")
def workspaces_delete(ws_id: str) -> dict:
    try:
        ok = ws_store.delete_workspace(ws_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="workspace not found")
    return {"deleted": ws_id}


# --- Web research -----------------------------------------------------------

@app.post("/api/research")
def web_research(req: WebResearchRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    """Run a web_search query, save the synthesized findings as a markdown doc
    in the active workspace, then rebuild the graph so the new doc is indexed.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is empty")
    client = _anthropic_client()
    if not client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    system = (
        "You are a research assistant. Use the web_search tool to investigate the "
        "user's query, then write a concise Markdown summary of what you found. "
        "Structure: # Title\\n\\nOne-paragraph TL;DR.\\n\\n## Key findings (bullets, "
        "each ending with a 'web: <domain>' citation).\\n\\n## Open questions (3-5 "
        "bullets). Keep it ~400-700 words. Cite every claim."
    )
    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_RESEARCH_MODEL", "claude-sonnet-4-6"),
            max_tokens=2500,
            system=system,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": int(os.environ.get("GRAPHIFY_RESEARCH_WEB_MAX_USES", "6")),
            }],
            messages=[{"role": "user", "content": req.query.strip()}],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"web research failed: {exc}")

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
    body = "\n".join(p for p in text_parts if p).strip()
    if not body:
        raise HTTPException(status_code=502, detail="web research returned no content")

    # Append a Sources block so the saved doc is self-contained.
    if web_sources:
        body += "\n\n## Sources\n" + "\n".join(
            f"- [{s['title'] or s['url']}]({s['url']})" for s in web_sources
        )

    # Default a filename from the query if not provided.
    safe = (req.filename or req.query)[:80].strip().replace("/", "-").replace("\\", "-")
    safe = "".join(c for c in safe if c.isalnum() or c in " -_") or "web-research"
    fname = f"web-{safe.replace(' ', '-')}.md"
    path = save_upload(ws, fname, body.encode("utf-8"))
    job = _start_rebuild_job(ws, kind="web_research", label=f"Indexing web research: {fname}")
    return {"saved": path.name, "web_sources": web_sources, "job": job}


# --- Playbooks --------------------------------------------------------------

@app.get("/api/playbooks")
def playbooks_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {
        "playbooks": playbooks.list_playbooks(ws),
        "artifact_types": artifacts.ARTIFACT_TYPES,
        "horizons": playbooks.horizon_options(),
        "synth_inference_strategies": sorted(playbooks.SYNTH_INFERENCE_STRATEGIES),
        "step_types": sorted(playbook_store.VALID_STEP_TYPES),
    }


@app.get("/api/playbooks/custom")
def playbooks_custom_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"playbooks": playbook_store.list_playbooks(ws)}


@app.get("/api/playbooks/source/{pid}")
def playbooks_get_full(pid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    """Return the full playbook definition (steps with all fields). Override
    wins over built-in; source distinguishes builtin / customized / scope."""
    override = playbook_store.get_playbook(ws, pid)
    if pid in playbooks.PLAYBOOKS:
        if override:
            pb = dict(override)
            pb["source"] = "customized"
            return pb
        pb = dict(playbooks.PLAYBOOKS[pid])
        pb["source"] = "builtin"
        return pb
    if not override:
        raise HTTPException(status_code=404, detail="playbook not found")
    override["source"] = override.get("scope", "workspace")
    return override


class PlaybookSpecRequest(BaseModel):
    id: str | None = None
    label: str
    tagline: str = ""
    expected_duration_s: int = 240
    accepts_source_types: list[str] = []
    artifact_type: str
    steps: list[dict[str, Any]]
    scope: str = "workspace"


@app.post("/api/playbooks/custom")
def playbooks_custom_create(req: PlaybookSpecRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.id:
        raise HTTPException(status_code=400, detail="id is required")
    try:
        return playbook_store.create_playbook(ws, req.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/playbooks/custom/{pid}")
def playbooks_custom_update(pid: str, req: PlaybookSpecRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        updated = playbook_store.update_playbook(ws, pid, req.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="playbook not found")
    return updated


@app.delete("/api/playbooks/custom/{pid}")
def playbooks_custom_delete(pid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not playbook_store.delete_playbook(ws, pid):
        raise HTTPException(status_code=404, detail="playbook not found")
    return {"deleted": pid}


@app.get("/api/playbooks/{pid}/suggest-scenarios")
def playbooks_suggest_scenarios(
    pid: str, fresh: bool = False, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Return 3 corpus-grounded scenario suggestions for the kickoff textarea.
    Cached for 15 min per (workspace, playbook); pass ?fresh=true to bypass
    and re-roll (wired to the kickoff "↻ Refresh" button). Empty list when
    there's no graph/insights yet, no API key, or the LLM can't be reached —
    the UI falls back to its static placeholder."""
    return {"scenarios": playbooks.suggest_scenarios(ws, pid, fresh=fresh)}


@app.post("/api/playbooks/{pid}/restore-default")
def playbooks_restore(pid: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if pid not in playbooks.PLAYBOOKS:
        raise HTTPException(status_code=400, detail="not a built-in playbook")
    playbook_store.restore_builtin_playbook(ws, pid)
    pb = dict(playbooks.PLAYBOOKS[pid])
    pb["source"] = "builtin"
    return pb


class PlaybookCloneRequest(BaseModel):
    new_id: str | None = None
    scope: str = "workspace"


@app.post("/api/playbooks/{pid}/clone")
def playbooks_clone(pid: str, req: PlaybookCloneRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        return playbook_store.clone_playbook(ws, pid, new_id=req.new_id, scope=req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/playbooks/run")
def playbooks_run(req: PlaybookRunRequest, ws: Workspace = Depends(active_workspace)) -> dict:
    if not req.scenario.strip():
        raise HTTPException(status_code=400, detail="scenario is required")
    try:
        run = playbooks.start_run(
            ws,
            playbook_id=req.playbook_id,
            scenario=req.scenario,
            horizon=req.horizon,
            source_artifact_id=req.source_artifact_id,
            rubric_id=req.rubric_id,
            web_grounding=req.web_grounding,
            synth_inference_strategy=req.synth_inference_strategy,
            fact_check=req.fact_check,
            answer_model=req.answer_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return run


@app.get("/api/playbooks/runs")
def playbooks_runs_list(ws: Workspace = Depends(active_workspace)) -> dict:
    return {"runs": playbooks.list_runs(ws)}


@app.get("/api/playbooks/runs/{run_id}")
def playbooks_run_get(run_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    r = playbooks.get_run(ws, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run not found")
    return r


@app.delete("/api/playbooks/runs/{run_id}")
def playbooks_run_delete(run_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not playbooks.delete_run(ws, run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"deleted": run_id}


@app.post("/api/playbooks/runs/{run_id}/cancel")
def playbooks_run_cancel(run_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    r = playbooks.request_cancel(ws, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return r


@app.post("/api/playbooks/runs/{run_id}/resume")
def playbooks_run_resume(run_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    try:
        r = playbooks.resume_run(ws, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return r


# --- Artifacts --------------------------------------------------------------

@app.get("/api/artifacts")
def artifacts_list(
    artifact_type: str | None = None,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    return {
        "artifacts": artifacts.list_artifacts(ws, artifact_type=artifact_type),
        "types": artifacts.ARTIFACT_TYPES,
    }


class ArtifactCreateRequest(BaseModel):
    artifact_type: str
    title: str = ""
    tldr: str = ""
    sections: dict[str, str] | None = None
    raw_markdown: str
    highlights: list[dict[str, str]] | None = None
    provenance: dict[str, Any] | None = None


@app.post("/api/artifacts")
def artifacts_create(
    req: ArtifactCreateRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Create an artifact from arbitrary text — used by Conversations / ForeSight
    'Send to Artifacts' actions, manual notes, and brownfield playbook stages
    that don't use the standard playbook synth pipeline."""
    try:
        art = artifacts.create_artifact(
            ws,
            artifact_type=req.artifact_type,
            title=req.title,
            tldr=req.tldr,
            sections=req.sections,
            raw_markdown=req.raw_markdown,
            highlights=req.highlights,
            provenance=req.provenance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return art


@app.get("/api/artifacts/{art_id}")
def artifacts_get(art_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    a = artifacts.get_artifact(ws, art_id)
    if not a:
        raise HTTPException(status_code=404, detail="artifact not found")
    return a


@app.delete("/api/artifacts/{art_id}")
def artifacts_delete(art_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    if not artifacts.delete_artifact(ws, art_id):
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"deleted": art_id}


class ArtifactRenameRequest(BaseModel):
    title: str


@app.patch("/api/artifacts/{art_id}")
def artifacts_rename(
    art_id: str,
    req: ArtifactRenameRequest,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    try:
        art = artifacts.rename_artifact(ws, art_id, req.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    return art


# ---- Follow-up Q&A + plain-language rewrite ----

class ArtifactAskRequest(BaseModel):
    question: str
    answer_model: str | None = None


@app.post("/api/artifacts/{art_id}/ask")
def artifacts_ask(
    art_id: str, req: ArtifactAskRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    try:
        entry = artifacts.ask_artifact(
            ws, art_id, req.question, answer_model=req.answer_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if entry is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return entry


@app.get("/api/artifacts/{art_id}/qa")
def artifacts_qa(art_id: str, ws: Workspace = Depends(active_workspace)) -> dict:
    art = artifacts.get_artifact(ws, art_id)
    if not art:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"qa_history": art.get("qa_history", [])}


@app.delete("/api/artifacts/{art_id}/qa/{qa_id}")
def artifacts_delete_qa(
    art_id: str, qa_id: str, ws: Workspace = Depends(active_workspace),
) -> dict:
    if not artifacts.delete_qa_entry(ws, art_id, qa_id):
        raise HTTPException(status_code=404, detail="qa entry not found")
    return {"deleted": qa_id}


class ArtifactSimplifyRequest(BaseModel):
    force: bool = False
    answer_model: str | None = None


@app.post("/api/artifacts/{art_id}/simplify")
def artifacts_simplify(
    art_id: str, req: ArtifactSimplifyRequest | None = None,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    body = req or ArtifactSimplifyRequest()
    try:
        simplified = artifacts.simplify_artifact(
            ws, art_id, force=body.force, answer_model=body.answer_model,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if simplified is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return simplified


class CommentCreate(BaseModel):
    text: str
    author: str = ""
    section: str | None = None


class CommentUpdate(BaseModel):
    status: str | None = None
    text: str | None = None


@app.post("/api/artifacts/{art_id}/comments")
def artifacts_add_comment(
    art_id: str, req: CommentCreate, ws: Workspace = Depends(active_workspace),
) -> dict:
    try:
        c = artifacts.add_comment(
            ws, art_id, text=req.text, author=req.author, section=req.section,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if c is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return c


@app.patch("/api/artifacts/{art_id}/comments/{comment_id}")
def artifacts_update_comment(
    art_id: str,
    comment_id: str,
    req: CommentUpdate,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    try:
        c = artifacts.update_comment(
            ws, art_id, comment_id, status=req.status, text=req.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if c is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return c


class ArtifactRefineRequest(BaseModel):
    # Optional free-form refinement instruction. When provided, it's persisted
    # as a document-level comment so refine_artifact picks it up alongside any
    # open reviewer comments. Empty body = "refine using existing open comments
    # only" (legacy behavior).
    instruction: str | None = None
    author: str | None = None
    # Optional context sources to fold into the refine prompt.
    include_qa: bool = False
    include_conversation: bool = False


class SuggestPatchRequest(BaseModel):
    parent_id: str
    from_version: int | None = None
    to_version: int | None = None


@app.post("/api/artifacts/{art_id}/suggest-patch")
def artifacts_suggest_patch(
    art_id: str, req: SuggestPatchRequest, ws: Workspace = Depends(active_workspace),
) -> dict:
    """Diff-aware cascade: given a child artifact and a parent whose newer
    version contains material changes, propose targeted patches to the child.
    Does NOT mutate the child."""
    try:
        suggestion = playbooks.suggest_patch_from_parent(
            ws, art_id, req.parent_id,
            from_version=req.from_version,
            to_version=req.to_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return suggestion


@app.post("/api/artifacts/{art_id}/refine")
def artifacts_refine(
    art_id: str,
    req: ArtifactRefineRequest | None = None,
    ws: Workspace = Depends(active_workspace),
) -> dict:
    body = req or ArtifactRefineRequest()
    instruction = (body.instruction or "").strip()
    if instruction:
        try:
            artifacts.add_comment(
                ws, art_id,
                text=instruction,
                author=(body.author or "Refine instruction").strip() or "Refine instruction",
                section=None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        art = playbooks.refine_artifact(
            ws, art_id,
            include_qa=body.include_qa,
            include_conversation=body.include_conversation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return art


# --- Static frontend --------------------------------------------------------
# Built React assets land in app/frontend/dist. If the build doesn't exist yet
# (e.g. backend is started before `npm run build`), the API still works on its own.

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIR.is_dir():
    # Mount static assets at /assets so it doesn't shadow the API.
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa_fallback(path: str) -> FileResponse:
        # API routes are matched first because they're declared above.
        candidate = FRONTEND_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    @app.get("/")
    def root_no_frontend() -> dict:
        return {
            "message": "Frontend not built yet. Run `npm run build` in app/frontend.",
            "api_docs": "/docs",
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    # Reload-on by default for dev; set UVICORN_NO_RELOAD=1 to disable.
    reload = os.environ.get("UVICORN_NO_RELOAD", "").lower() not in ("1", "true", "yes")
    if reload:
        # reload mode needs an import string + a `reload_dirs` so uvicorn knows
        # which tree to watch (the backend directory containing this file).
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=port,
            reload=True,
            reload_dirs=[str(Path(__file__).parent)],
        )
    else:
        uvicorn.run(app, host="0.0.0.0", port=port)
