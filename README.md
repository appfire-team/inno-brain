# InnoBrain

**Turn a folder of PDFs, code, and links into an interactive knowledge graph
you can chat with.**

You upload documents (PDFs, markdown, images, code repos, URLs). InnoBrain
asks Claude to read them, pulls out the important *concepts* and the
*relationships between them*, and stores everything as a graph you can
explore, query, and refine by hand. Then you can:

- chat with the graph and get cited, sourced answers
- run multi-step "playbooks" that produce typed outputs (a PRD draft, a
  build-vs-buy decision, an opportunity scan, a launch plan…)
- pressure-test ideas through scenarios with multiple AI personas (ForeSight)
- correct the graph when it gets a fact wrong — your edits stick

It runs as one process on your laptop. No database, no cloud account
beyond an Anthropic API key.

![Guide tab](guide-tab-rendered.png)

> Brand note: the product is **InnoBrain** in the UI; the codebase was
> originally called **graphify web**, and the GitHub repo is
> [`appfire-team/inno-brain`](https://github.com/appfire-team/inno-brain).

---

## Quickstart (5 minutes)

**Prerequisites**

- Python **3.10+**
- Node **18+** and npm
- An Anthropic API key — get one at https://console.anthropic.com
- macOS or Linux (Windows: works under WSL2)

**Steps**

```bash
git clone https://github.com/appfire-team/inno-brain.git
cd inno-brain

# 1. Backend: install deps and add your API key
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Open .env and paste your key into ANTHROPIC_API_KEY=

# 2. Frontend: install + build
cd ../frontend
npm install
npm run build

# 3. Run (one process serves API + UI)
cd ../backend
python3 main.py
```

Open <http://localhost:8000>. That's it.

> Prefer not to use a venv? You can run `pip install -r requirements.txt`
> directly, but on macOS Homebrew-managed Python you'll need to add
> `--break-system-packages`. The venv path above avoids that footgun.

---

## Your first 60 seconds in the app

1. The UI opens on the **Conversations** tab with a Default workspace.
2. In the left **Sidebar**, drag a PDF or click **Upload**. A background job
   indexes it (you'll see a banner). For small docs this takes ~30 seconds.
3. Once indexed, type a question in the chat: *"Summarize the main
   argument."* The answer comes with citations to the source filename and
   a `gaps` block listing things the graph doesn't know.
4. Click the **Graph** tab to see the document as a network. Click any node
   to open the **Explain drawer**.
5. Try the **Communities** tab — clusters of related concepts the system
   discovered and labeled.

When something is wrong, go to **Refine KB** and add a correction — it
overlays on top of the graph and shows up in future answers without
re-running the pipeline.

---

## The tabs at a glance

| Tab | What it does |
|-----|--------------|
| **Conversations** | Threaded chat over the graph. Pick an intent (explore / decide / challenge…) and an inference strategy (`reflection`, `cove`, `best_of_3`) per thread. Optional web grounding. |
| **Playbooks** | Multi-step workflows that produce **Artifacts** — e.g. `OpportunityScan`, `PRDDraft`, `BuildBuyDecision`, `LaunchPlan`, `PremortemPlan`, `CodebaseHealth`. Runs are resumable, cancellable, and refinable via reviewer comments. |
| **ForeSight** | Multi-persona scenario sessions across horizons (6mo / 1y / 3y). Pick from preset personas (bull, bear, customer, competitor…) or define your own. |
| **Artifacts** | The library of typed outputs produced by playbooks, plus manual notes. Each artifact supports Q&A, simplification, comments, and inline refinement. |
| **Ask Graph** | One-shot BFS/DFS query — lighter than a full conversation turn, good for "what nodes mention X?" probes. |
| **Graph** | Force-directed 2D visualization. Click nodes to explain; filter by community. |
| **Communities** | Louvain-clustered groups of related concepts, auto-labeled by Claude. |
| **Insights** | Precomputed "god nodes" (hubs), surprising cross-community links, and suggested questions. |
| **Path** | Shortest path between any two concepts in the graph. |
| **Refine KB** | Human corrections (Fix / Add / Confirm / Doubt) that overlay the graph at read time. Your edits never modify the underlying extraction. |
| **Guide** | In-app onboarding + user guide. |

The header has a **workspace switcher** — workspaces are fully isolated
corpora (their own docs, graph, conversations, memory, artifacts).

---

## Run modes

**Production-style (one port, recommended for sharing via ngrok):**

```bash
cd frontend && npm run build
cd ../backend && python3 main.py
# → http://localhost:8000 (API + built UI)
ngrok http 8000        # optional public URL
```

**Dev mode (HMR for the frontend):**

```bash
# Terminal 1
cd backend && python3 main.py

# Terminal 2
cd frontend && npm run dev
# → http://localhost:5173 (Vite proxies /api → :8000)
```

`uvicorn` runs with `reload=True` by default. Set `UVICORN_NO_RELOAD=1`
to disable it (e.g. when running under a debugger).

---

## Configuration

Set in `backend/.env`. Full list lives in [`ARCHITECTURE.md` §9](ARCHITECTURE.md#9-configuration); the most-used knobs:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | **Required.** All LLM features depend on this. |
| `GRAPHIFY_MODEL` | `claude-sonnet-4-6` | Per-document semantic extraction |
| `GRAPHIFY_ANSWER_MODEL` | `claude-haiku-4-5-20251001` | Default Q&A + explain synthesis |
| `GRAPHIFY_CONCURRENCY` | `3` | Parallel extraction workers |
| `GRAPHIFY_AUTOLINK` | `1` | Run cross-doc linker on rebuild (`0` to skip) |
| `OPENAI_API_KEY` / `VOYAGE_API_KEY` | — | Enable the optional semantic-vector index |
| `PORT` | `8000` | uvicorn listen port |
| `UVICORN_NO_RELOAD` | `0` | Set `1` to disable reload mode |

Model IDs the UI lets users pick are pinned in `backend/main.py` (`ANSWER_MODELS`):
`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-7`.

---

## Data layout

Everything lives under `backend/data/` and is gitignored:

```
backend/data/
├── workspaces/<ws-id>/        # one subtree per workspace
│   ├── workspace.json         # name, created_at, source_workspace_id
│   ├── raw/                   # uploaded docs, ingested URLs, copied repos
│   ├── graphify-out/
│   │   ├── graph.json         # NetworkX node-link export
│   │   ├── insights.json      # gods, surprises, communities, questions
│   │   ├── embeddings.npz     # optional vector index
│   │   └── sem-cache/         # extraction cache keyed by file hash
│   ├── conversations/         # one JSON per thread
│   ├── artifacts/             # typed outputs from playbooks + manual notes
│   ├── playbook_runs/         # run state, step traces, resume cursors
│   ├── foresight/             # session transcripts
│   ├── rubrics/               # overrides + workspace-only rubrics
│   ├── intents/               # custom + overridden conversation intents
│   ├── playbooks/             # custom + overridden playbook definitions
│   ├── kb_corrections/        # human refinements on nodes/edges
│   └── memory.json            # durable facts injected into every turn
├── global_intents/            # cross-workspace user-defined intents
├── global_playbooks/          # cross-workspace user-defined playbooks
└── foresight_personas/        # user-defined personas (shared across workspaces)
```

API clients select a workspace by sending `X-Workspace-Id: <ws-id>`. With
no header, the most-recently-updated workspace wins. A `default` workspace
is created automatically on first run (and any pre-workspace data is
migrated into it).

---

## What's *not* here

- **No database.** Per-workspace JSON on local disk is the entire storage
  model. This is deliberate — single-tenant, demo-friendly, trivial to
  inspect and back up.
- **No authentication.** CORS is wide open. Both are conscious tradeoffs
  for the local + ngrok deployment model — see [`ARCHITECTURE.md` §11](ARCHITECTURE.md#11-security-and-operational-notes) before exposing publicly.
- **No automated test suite.** Verify changes with `npm run build`
  (catches TS regressions via `tsc -b`), `curl http://localhost:8000/api/...`,
  the `/docs` Swagger UI, and the matching panel in the app.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Frontend not built yet. Run npm run build in frontend/.` at `/` | You started the backend without building the frontend. Run `cd frontend && npm run build`. The API still works at `/docs`. |
| `Address already in use: 8000` | Another process holds the port. `lsof -i :8000` to find it, or set `PORT=8001`. |
| Upload returns `meta.error: 'ANTHROPIC_API_KEY missing'` | `.env` not loaded. Confirm `backend/.env` exists and contains a valid key, then restart the backend. |
| Pipeline hangs at "extracting…" forever | Check `/api/index-job` for status. Large PDFs take ~60s each; many docs run in parallel up to `GRAPHIFY_CONCURRENCY`. |
| TLS errors when calling Anthropic from a corporate network | `truststore.inject_into_ssl()` is already wired in `main.py` — make sure your OS trust store has the corporate root cert (Netskope, ZScaler, etc.). |
| `pip install` complains about `externally-managed-environment` | macOS Homebrew Python. Use the `python3 -m venv .venv` step from Quickstart, or add `--break-system-packages` (not recommended). |
| Graph looks empty after upload | The pipeline may still be running. The header pill shows node count; refresh after the job banner disappears. |

---

## Where to read next

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — components, data contracts, pipeline stages, API reference.
- **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** — plain-language "lego" walkthrough of ingest + query.
- **[COMPANY_BRAIN_ROADMAP.md](COMPANY_BRAIN_ROADMAP.md)** — product direction.
- **[COMPANY_BRAIN_USECASES.md](COMPANY_BRAIN_USECASES.md)** — target workflows.
- **[CLAUDE.md](CLAUDE.md)** — conventions for AI coding agents working in this repo.

For the interactive API reference, run the backend and open
<http://localhost:8000/docs>.
