# InnoBrain (graphify web)

Browser-based knowledge-graph workbench. Upload documents, code repos, or URLs;
the backend extracts a graph with Anthropic Claude and exposes it through
conversations, multi-step playbooks, scenario simulations (ForeSight), and
typed artifacts.

The UI brand is **InnoBrain**; the repo + package names use **graphify web**.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for a deep dive into components,
pipeline stages, and data contracts. See [`COMPANY_BRAIN_ROADMAP.md`](COMPANY_BRAIN_ROADMAP.md)
for the product direction.

## Stack

- **Backend** — FastAPI (Python 3.10+). One process serves the REST API at
  `/api/*` and (in prod) the built React assets. Uses the `graphifyy` library
  for the deterministic graph pipeline and Claude for extraction / linking /
  labeling / answer synthesis.
- **Frontend** — Vite + React 18 + TypeScript. `react-force-graph-2d` for
  the graph view, `react-markdown` + `mermaid` for rich answers.
- **Storage** — Local filesystem under `backend/data/`. Workspaces are
  isolated subtrees; there is no database.
- **LLM** — `ANTHROPIC_API_KEY` required for extraction and every LLM
  feature. Optional `OPENAI_API_KEY` / `VOYAGE_API_KEY` enables the
  semantic-vector retrieval index (otherwise the embeddings module no-ops).

## What's in here

| Surface | What it does |
|---------|--------------|
| **Conversations** | Threaded chat grounded in the graph + persistent memory + rubrics + optional web grounding. Inference strategies: `none`, `reflection`, `cove`, `best_of_3`. |
| **Playbooks** | Multi-step LLM workflows that produce typed **Artifacts** (OpportunityScan, PRDDraft, BuildBuyDecision, LaunchPlan, …). Built-ins + user-defined; runs are resumable, cancellable, and re-refinable via reviewer comments. |
| **ForeSight** | Multi-persona scenario sessions across horizons (6mo / 1y / 3y). Personas are presets + user-defined; sessions can borrow context from a conversation. |
| **Ask Graph** | One-shot BFS/DFS query over the graph with optional synthesized answer. |
| **Graph / Communities / Path / Insights** | Force-directed visualization, labeled clusters, shortest-path explorer, and precomputed god-nodes / surprises / suggested questions. |
| **Refine KB** | Human corrections + attestations on graph nodes/edges, surfaced into future LLM answers. |
| **Workspaces** | First-class isolation: each workspace has its own `raw/`, graph, conversations, memory, rubrics, artifacts, playbook runs, ForeSight sessions. |

## One-time setup

```bash
# Backend
cd app/backend
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
python3 -m pip install -r requirements.txt --break-system-packages

# Frontend
cd ../frontend
npm install
npm run build
```

## Run

```bash
# Single-process (API + built static SPA)
cd app/backend
python3 main.py
# → http://localhost:8000 — interactive API docs at /docs

# Optional public URL
ngrok http 8000
```

## Dev mode (HMR for the frontend)

```bash
# Terminal 1
cd app/backend && python3 main.py

# Terminal 2
cd app/frontend && npm run dev
# → http://localhost:5173 (Vite proxies /api → :8000)
```

`uvicorn` is launched with `reload=True` by default. Set
`UVICORN_NO_RELOAD=1` to disable, e.g. when running under a debugger.

## Data layout

Everything is under `backend/data/` and gitignored:

```
backend/data/
├── workspaces/<ws-id>/        # one subtree per workspace
│   ├── raw/                   # uploaded docs, ingested URLs, copied repos
│   ├── graphify-out/
│   │   ├── graph.json         # NetworkX node-link export
│   │   └── insights.json      # gods, surprises, communities, questions
│   ├── conversations/         # one JSON per thread
│   ├── artifacts/             # typed outputs from playbooks + manual notes
│   ├── playbook_runs/         # run state, step traces, resume cursors
│   ├── foresight/             # session transcripts
│   ├── rubrics/               # overrides + workspace-only rubrics
│   ├── intents/               # custom + overridden conversation intents
│   ├── playbooks/             # custom + overridden playbook definitions
│   ├── memory.json            # durable facts injected into every turn
│   └── kb_corrections.json    # human refinements on graph nodes/edges
├── global_intents/            # cross-workspace user-defined intents
├── global_playbooks/          # cross-workspace user-defined playbooks
├── foresight_personas/        # user-defined / customized personas
└── rubrics/                   # legacy / pre-workspace rubrics
```

API clients select a workspace by sending `X-Workspace-Id: <ws-id>`. With no
header, the most-recently-updated workspace wins. A `default` workspace is
created automatically on first run (and existing pre-workspace data is
migrated into it).

## Configuration

Set in `backend/.env`. The full list lives in `ARCHITECTURE.md §9`; the
most-used knobs are:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required |
| `GRAPHIFY_MODEL` | `claude-sonnet-4-6` | Per-document semantic extraction |
| `GRAPHIFY_ANSWER_MODEL` | `claude-haiku-4-5-20251001` | Default Q&A / explain synth |
| `GRAPHIFY_CONCURRENCY` | `3` | Parallel extraction workers |
| `GRAPHIFY_AUTOLINK` | `1` | Run cross-doc linker on rebuild (`0` to skip) |
| `OPENAI_API_KEY` / `VOYAGE_API_KEY` | — | Enable the semantic-vector index |
| `PORT` | `8000` | uvicorn listen port |
| `UVICORN_NO_RELOAD` | `0` | Set `1` to disable reload mode |

## Notes for operators

- Permissive CORS (`*`) is intentional for local Vite dev. Tighten it
  before exposing the API publicly.
- Uploads cap at 50 MB per file; empty files are skipped.
- Long-running rebuilds run on a background thread; `/api/index-job`
  exposes status for the UI banner. Orphaned runs (from a prior process)
  are swept to `failed` on startup.
- `truststore.inject_into_ssl()` routes TLS through the OS trust store
  so corporate MITM proxies (e.g. Netskope) don't break the Anthropic
  client.
