# CLAUDE.md — guidance for AI coding assistants in this repo

This file orients agents (Claude Code, etc.) working in this repo. It captures
conventions and non-obvious wiring that you can't infer from a single file.
For the full architecture overview, read [`ARCHITECTURE.md`](ARCHITECTURE.md).

## What this app is

InnoBrain / graphify web — a single-deployable knowledge-graph workbench.
FastAPI (`backend/main.py`) serves both the REST API at `/api/*` and the
built React SPA from `frontend/dist/`. Data lives on the local filesystem
under `backend/data/`; there is no database.

Major surfaces in the UI (each is a tab in `frontend/src/App.tsx`):
**Playbooks**, **Conversations**, **ForeSight**, **Artifacts**, **Ask Graph**,
**Graph**, **Communities**, **Insights**, **Path**, **Refine KB**, **Guide**.

## Project layout

```
.
├── backend/
│   ├── main.py                 # FastAPI routes; thin — delegates to modules below
│   ├── graphify_runner.py      # Pipeline orchestration + query_graph + rich_query
│   ├── workspaces.py           # Workspace CRUD; resolves the X-Workspace-Id header
│   ├── conversations.py        # Thread store (one JSON per conversation)
│   ├── memory.py               # Durable facts injected into every turn
│   ├── rubrics.py              # Built-in intents + rubrics registry
│   ├── intent_store.py         # User-defined / overridden intents
│   ├── playbooks.py            # Built-in playbooks + run engine
│   ├── playbook_store.py       # User-defined / overridden playbooks
│   ├── artifacts.py            # Typed outputs (PRDDraft, OpportunityScan, …)
│   ├── foresight.py            # Multi-persona scenario sessions
│   ├── simulate.py             # Inline conversation simulation
│   ├── kb_corrections.py       # Human refinements on graph nodes/edges
│   ├── entity_extract.py       # Deterministic entity typing + role edges
│   ├── embeddings.py           # Optional semantic-vector index (OpenAI / Voyage)
│   ├── index_jobs.py           # Background-rebuild job tracker
│   └── data/                   # Runtime data (gitignored — see README "Data layout")
└── frontend/
    └── src/
        ├── App.tsx             # Shell: workspace switcher, tabs, drawers
        ├── api.ts              # Typed fetch wrappers; always send X-Workspace-Id
        ├── components/         # One file per tab/panel/drawer
        ├── guides/             # In-app markdown user guides
        ├── hooks/
        └── utils/
```

## Conventions that matter

### Workspaces are required scope

Almost every backend handler depends on `active_workspace`, which reads
`X-Workspace-Id` from the request header (falling back to the most-recently-
updated workspace if absent). When adding endpoints, follow the existing
pattern:

```python
@app.get("/api/something")
def something(ws: Workspace = Depends(active_workspace)) -> dict:
    ...
```

Data writes go under `ws.path`. Never write to a hardcoded `backend/data/raw`
path — that's pre-workspace and only exists for the legacy-migration shim
in `ensure_default_workspace`.

### Built-ins are overridable, not editable

Intents, rubrics, playbooks, and ForeSight personas all follow the same
pattern: there's a built-in registry in code (`rubrics.INTENT_LABELS`,
`playbooks.PLAYBOOKS`, `foresight._PRESET_PERSONAS`, …) and a per-workspace
or global override store on disk. Editing a built-in materializes an
override; `restore-default` deletes the override. When adding new built-ins,
update the in-code registry — don't write directly to the store.

### Long pipeline work runs on a background thread

Any handler that triggers `rebuild_graph` (upload, delete, ingest-url,
ingest-repo, /rebuild, /research) wraps it via `_start_rebuild_job`, which
delegates to `index_jobs.start(...)`. Handlers return the job descriptor
immediately; the UI polls `/api/index-job`. Don't call `rebuild_graph`
directly from a handler — you'll re-introduce request timeouts on large
corpora and you'll bypass the orphan-sweep on startup.

`_PIPELINE_LOCK` inside `graphify_runner` serializes concurrent rebuilds
across workspaces; respect it.

### The two query paths are intentionally different

- `query_graph` (used by `/api/query`, the "Ask Graph" tab) — tokenize → score
  node labels → BFS/DFS → optional `synthesize_answer`. One-shot.
- `rich_query` (used by `/api/conversations/{id}/turn`) — LLM router picks
  entry nodes, then BFS, then runs the configured inference strategy, with
  conversation history + intent + rubric + memory + optional web grounding.

Don't merge them. Ask Graph is the lightweight surface; Conversations is the
full one.

### Answer-model selection lives in code

`ANSWER_MODELS` in `main.py` is the canonical list of model IDs the UI lets
users pick. If you add a new Claude model, update both `ANSWER_MODELS` and
the relevant `GRAPHIFY_*_MODEL` defaults — they drift easily.

Current canonical model IDs (as of writing):
- `claude-haiku-4-5-20251001` — fast/cheap default for synth + labels
- `claude-sonnet-4-6` — balanced default for extraction + linker + research
- `claude-opus-4-7` — highest quality, slowest

### Errors that should surface vs swallow

- Validation errors at the API boundary → `HTTPException` (400/404/409/503
  as appropriate). The UI relies on the status codes.
- LLM failures inside synth steps (Ask Graph synthesize, web research,
  insights enrichment) → catch and put the error string into the response
  payload so the rest of the result still flows. The UI keeps working;
  the user sees a per-section failure note.
- Pipeline failures inside background jobs → let them propagate;
  `index_jobs` records the failure and the UI shows a toast.

## Running things

```bash
# Backend (reload mode on by default)
cd backend && python3 main.py     # → http://localhost:8000

# Frontend dev (HMR; proxies /api → 8000)
cd frontend && npm run dev        # → http://localhost:5173

# Production-style build
cd frontend && npm run build      # writes frontend/dist/
cd backend  && python3 main.py    # serves both API + static SPA
```

There is no automated test suite. Verify changes by:
1. `npm run build` — type-checks via `tsc -b` first, so this catches TS regressions.
2. Hit the relevant endpoint with `curl http://localhost:8000/api/...` or via `/docs` (FastAPI's Swagger UI).
3. Drive the matching panel in the UI.

## Things to avoid

- Don't introduce new top-level directories outside `backend/` and
  `frontend/`. Data goes under `backend/data/<workspace>/`.
- Don't add a database. The local-FS / per-workspace JSON model is a
  deliberate constraint (single-tenant, demo-friendly, easy to inspect).
- Don't hardcode model IDs in module code — read from env with the
  `GRAPHIFY_*_MODEL` defaults so users can override.
- Don't loosen CORS further than it already is, and don't add auth as
  a side-effect of an unrelated change — both are conscious tradeoffs
  for the local/ngrok deployment model. See `ARCHITECTURE.md §11`.
- Don't bypass `_start_rebuild_job` for any pipeline-triggering route.
- Don't write new comments unless they explain *why*. The existing
  inline comments are tuned for that — match the style.

## When in doubt

- For pipeline / extraction / linking questions: read `graphify_runner.py`
  and the upstream `graphifyy` package.
- For UI behavior: the tab component (`components/<Name>Panel.tsx`) is
  usually self-contained; `api.ts` is the single fetch boundary.
- For product intent: `COMPANY_BRAIN_ROADMAP.md` is the latest direction
  document; `COMPANY_BRAIN_USECASES.md` enumerates target use cases.
