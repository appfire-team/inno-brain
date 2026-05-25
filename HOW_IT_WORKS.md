# How the KB and Search work — the lego version

A plain-language explainer of how InnoBrain turns documents into a graph
and turns questions into well-cited answers. It uses two layers that
look similar but do very different jobs.

If you remember nothing else: **the KB is a graph, not a pile of text
chunks.** That single design choice is what makes everything below
possible.

---

## The yard: how to read the rest

Think of each piece as a lego block. Each block does **one thing**.
Blocks snap together in a fixed order — the same order every time —
and the output of one block is the input of the next. No magic. No
hidden state.

Three things you'll want to keep track of per block:

| Field | Meaning |
|-------|---------|
| **What** | One sentence: what it is |
| **Does** | One sentence: what it produces |
| **Cost** | LLM call (paid Claude API) or free (rule-based) |

---

# Part 1 — The KB

Two pipelines: **Ingest** (turns files into a graph) and **Refine**
(lets you edit the graph by hand). The graph itself lives in the
middle, on disk.

```
[ files ] ──► INGEST ──► [ graph.json ] ◄── REFINE ◄── [ your edits ]
```

## Layer 0 — Storage

### Block 0.1 — The graph file
- **What:** A single file at `backend/data/workspaces/<ws>/graphify-out/graph.json`
- **Does:** Holds every node and edge as plain JSON. NetworkX-compatible.
- **Cost:** Free (it's just a file)

That's it. The whole "knowledge base" is one JSON file per workspace.

## Layer 1 — Ingest (run on every upload, URL fetch, or repo ingest)

These eight blocks run in order. Each consumes the previous block's
output. Some are deterministic (free, fast); some call Claude.

### Block 1.1 — Reader
- **What:** A file-type sniffer
- **Does:** Looks at each file in `raw/` and decides: PDF? code? markdown? image?
- **Cost:** Free (file extension + content-type heuristic)

### Block 1.2 — AST extractor (code only)
- **What:** A Python/JS code parser
- **Does:** Walks the syntax tree of each code file, emits nodes for functions / classes / modules and edges for `calls`, `implements`
- **Cost:** Free (pure parsing, no LLM)

### Block 1.3 — Concept extractor (prose / PDF / image)
- **What:** A Claude prompt that reads a document
- **Does:** Emits 8–20 concept nodes per doc and 10–30 edges between them, with relations like `references`, `cites`, `conceptually_related_to`, `implements`
- **Cost:** **LLM call** (one per document, in parallel)

### Block 1.4 — Cross-doc linker
- **What:** A Claude prompt that compares node labels across all docs
- **Does:** Proposes `same_as` and `semantically_similar_to` edges to fuse the per-doc fragments into one graph
- **Cost:** **LLM call** (one, big prompt)

### Block 1.5 — Entity typer  ★ NEW
- **What:** A rule-based classifier on node labels
- **Does:** Tags each node with `entity_type` in `{person, company, organization, product}` when the label matches a heuristic (company suffix like "Inc.", all-caps acronyms like "NASA", two-or-three Title-Case tokens, trailing tokens like "University" / "Holdings")
- **Cost:** **Free** — no LLM, no NER model, just regex + a few word lists

### Block 1.6 — Role-edge extractor  ★ NEW
- **What:** A regex pass over plain-text source files (`.md`, `.txt`, `.html`)
- **Does:** Detects sentence patterns like `X works at Y`, `X founded Y`, `X invested in Y`, `X attended Y`, `X advises Y` and adds them as typed edges between existing graph nodes (matched by label)
- **Cost:** **Free** — pure regex, no LLM

### Block 1.7 — Community clusterer
- **What:** Louvain modularity algorithm
- **Does:** Groups densely-connected nodes into communities (numbered IDs)
- **Cost:** Free (deterministic graph algorithm)

### Block 1.8 — Community labeler
- **What:** A Claude prompt that names a cluster from its members
- **Does:** Turns "Community 7 (15 nodes)" into "Index Architecture & Hybrid Moat"
- **Cost:** **LLM call** (one per cluster, batched)

## Layer 2 — Refine (read-time merge, no extraction)

### Block 2.1 — Refinements store
- **What:** A separate JSON file per workspace with human edits
- **Does:** Stores Fix / Add / Confirm / Doubt items keyed to graph nodes or edges
- **Cost:** Free (just a file)

### Block 2.2 — Read-time merger
- **What:** A function that overlays refinements on the graph when an answer is being composed
- **Does:** Replaces the original extracted summary with your version, with attribution. Never mutates `graph.json`.
- **Cost:** Free

**Why this matters:** You can edit a fact without re-running the
pipeline. Your edit wins from then on. Delete the refinement → original
text comes back.

---

# Part 2 — Search (how a question becomes an answer)

This is the **query path**. Four blocks. The same path is used by
Conversations, Ask Graph, and every Playbook step that asks a question.

```
[ question ] ──► ROUTER ──► WALKER ──► SYNTHESIZER ──► [ answer + gaps ]
                                     │
                                     ▼
                        ( refinements merged in )
```

### Block 3.1 — The router
- **What:** A Claude prompt that picks where to start
- **Does:** Reads the question + conversation history, looks at the graph's labeled communities, and chooses 1–5 **entry nodes** that are most likely to anchor the answer
- **Cost:** **LLM call** (fast — Haiku-class model)

### Block 3.2 — The walker
- **What:** A breadth-first search algorithm
- **Does:** Starting from the entry nodes, walks 2–3 hops out, collects connected nodes + edges into a **subgraph**, and renders it as text the synthesizer can read
- **Cost:** Free (pure graph traversal, NetworkX)

### Block 3.3 — The refinements overlay
- **What:** Same as Block 2.2
- **Does:** Merges any human edits into the rendered subgraph text **before** the synthesizer sees it
- **Cost:** Free

### Block 3.4 — The synthesizer
- **What:** A Claude prompt with strict rules
- **Does:** Reads the question + rendered subgraph + your rubric + your persistent memory, writes a prose answer with citations (`source_file.pdf` or `web: domain.com`) and a structured `<gaps>…</gaps>` block at the end
- **Cost:** **LLM call** (one — Sonnet-class for Conversations, Haiku for Ask Graph)

### Block 3.5 — Gap parser  ★ NEW
- **What:** A regex that pulls `<gaps>…</gaps>` out of the response
- **Does:** Strips the gap block from the prose, returns the gaps as a structured list so the UI can render them in a separate amber-tinted block under the answer
- **Cost:** Free

### Optional Block 3.6 — Inference strategy
- **What:** A wrapper that runs the synthesizer multiple times in different ways
- **Does:** Implements `reflection` (draft → critique → revise), `cove` (draft → verify → revise), or `best_of_3` (sample 3 → pick)
- **Cost:** **2–3× the LLM cost** of a single synthesizer call

### Optional Block 3.7 — Web grounding
- **What:** A flag on the synthesizer that gives Claude its `web_search` tool
- **Does:** Lets the model verify time-sensitive claims (dates, prices, regulations) against the live web
- **Cost:** **Extra LLM tokens** for each web hit

## Direct-query path (skips the synthesizer entirely)

### Block 4.1 — The relations endpoint  ★ NEW
- **What:** A REST endpoint at `/api/entities/relations`
- **Does:** Filters the graph's typed edges directly — no LLM call. Answers structural questions like "who works at Acme?" or "what did Bob invest in?" in milliseconds
- **Cost:** **Free** — just a JSON filter over `graph.json`

---

# Part 3 — How the two claims compose from these blocks

## Claim A — *"A synthesis layer that gives you the actual answer, with citations and a gap list"*

Snap these blocks together in order:

```
question
   │
   ▼
[ 3.1 Router ]          ← LLM picks entry nodes
   │
   ▼
[ 3.2 Walker ]          ← BFS collects connected context
   │
   ▼
[ 3.3 Refinements ]     ← human edits overlay on top of extracted text
   │
   ▼
[ 3.4 Synthesizer ]     ← LLM writes prose + citations + <gaps> block
   │
   ▼
[ 3.5 Gap parser ]      ← splits prose from gaps
   │
   ▼
prose answer  +  ["gap 1", "gap 2", ...]
```

Every claim in GBrain's pitch maps to a block here:
- **"Synthesized, well-cited prose"** → Block 3.4 (the house style enforces `TL;DR` + 3–5 sentences + mandatory citations)
- **"Across people, companies, deals, ideas"** → Block 3.2 (the walker pulls connected subgraph, not isolated chunks)
- **"Explicit note on what the brain doesn't know"** → Block 3.5 (the gap parser surfaces the structured list)

## Claim B — *"A self-wiring knowledge graph with typed edges, zero LLM calls at write time, queries vector search can't reach"*

This is two snap-paths working together.

**At write time:**

```
file arrives in raw/
   │
   ▼
[ 1.1 Reader ]
   │
   ▼
[ 1.2 AST    ]  or  [ 1.3 Concept extractor ]
                                │
                                ▼
                       [ 1.4 Cross-doc linker ]
                                │
                                ▼
                       [ 1.5 Entity typer ]      ← FREE, zero LLM ★
                                │
                                ▼
                       [ 1.6 Role-edge extractor ] ← FREE, zero LLM ★
                                │
                                ▼
                       [ 1.7 Clusterer ]
                                │
                                ▼
                       [ 1.8 Community labeler ]
                                │
                                ▼
                       saved to graph.json
```

**At query time (the question vector search can't answer):**

```
"Who works at Acme AI?"
   │
   ▼
[ 4.1 Relations endpoint ]
   │
   ▼
filters graph.json for edges where relation=works_at AND target.label~="Acme"
   │
   ▼
[ Jane Doe, Bob Smith, Carol Lee (advises) ]
```

Notice: this path **never calls Claude.** Vector search can't answer
this because there's no "Acme" chunk to find — the answer requires
traversing the graph's typed edges. Snap blocks 1.5 + 1.6 + 4.1 together
and you get the capability.

## Honest scope vs the original claim

| Sub-claim | Block(s) involved | Status |
|---|---|---|
| Self-wiring on every page write | Blocks 1.5 + 1.6 run on every ingest | **Partial** — "ingest" not "page write" (no in-app authoring surface yet) |
| Entity refs extracted | Block 1.5 | **Yes** with the precision trade-off (heuristics over NER) |
| Typed edges (works_at, attended, founded, invested_in, advises) | Block 1.6 | **Yes** for those five relations |
| Zero LLM calls | Blocks 1.5 + 1.6 + 4.1 | **Yes** for the new pass; Claude still does the heavy lift in 1.3 / 1.4 / 1.8 |
| Entity-by-role queries | Block 4.1 | **Yes** via `/api/entities/relations` |
| Beyond vector search | Block 3.2 + 4.1 | **Yes** — we do real graph traversal, not similarity-only retrieval |

---

# Part 4 — How this is different from the neighbors

## vs. plain vector RAG ("here are 10 chunks that mention your query")

- Their KB is a **bag of text chunks** with embeddings
- Our KB is a **graph** with typed nodes, typed edges, communities, refinements
- Their "search" is **find chunks similar to query**
- Our search is **route to entry nodes, traverse, render, synthesize, hedge**
- Vector search can't follow a chain (`Bob → invested_in → Acme AI → founded_by → Jane`). The graph can.

## vs. Notion AI / ChatGPT-in-your-wiki

- They give you a chat box on top of your existing docs
- We turn the docs into a **structured graph** before any chat happens
- They have no concept of typed entities, role edges, communities, refinements, gaps
- Their answers are confident-sounding prose with no structured way to flag uncertainty. Ours always include the `gaps` list (Block 3.5).

## vs. GBrain's pitch

- **Synthesis layer** — we have it; the gap-analysis block (3.5) is the part we matched in the most recent change. Conversation turns and Ask Graph results both carry structured `gaps`.
- **Self-wiring entity graph** — we have *part* of it (Blocks 1.5 + 1.6 are deterministic, zero-LLM, role-typed). We don't have GBrain's @-mention write surface or per-page-write incremental indexing — we do per-ingest. That's a different write surface choice, not a fundamental limit.

---

# Part 5 — Quick reference: when to use what

| You want… | Use… | Why |
|-----------|------|-----|
| "Tell me about X. Cite your sources." | Conversations or Ask Graph (Blocks 3.1–3.5) | Synthesis + gaps + citations + refinements |
| "Who works at X?" / "What did Y invest in?" | `/api/entities/relations` (Block 4.1) | Structural, deterministic, zero LLM cost |
| "What entities did we tag in this corpus?" | `/api/entities` | Filter by Person / Company / Organization / Product |
| "What are the themes in our docs?" | Communities tab (Blocks 1.7 + 1.8) | Pre-labeled clusters |
| "Fix this fact / add a missing one" | Refine KB tab (Blocks 2.1 + 2.2) | Edits cite forward with attribution |
| "Stress-test this scenario" | ForeSight (separate doc) | Multi-persona, multi-horizon simulation on top of the same graph |
| "Turn an idea into shippable code" | Brownfield AI dev chain (Playbooks) | Seven typed steps, each citing the corpus |

---

# Appendix — Where to look in the code

| Concept | File |
|---------|------|
| Storage layout (Block 0.1) | `backend/workspaces.py` |
| Reader / detect (Block 1.1) | `graphify.detect` (upstream `graphifyy` package) |
| AST extractor (Block 1.2) | `graphify.extract` (upstream) |
| Concept extractor (Block 1.3) | `backend/graphify_runner.py` → `_extract_one`, `rich_semantic_extract` |
| Cross-doc linker (Block 1.4) | `backend/graphify_runner.py` → `_cross_document_link` |
| **Entity typer (Block 1.5)** ★ | `backend/entity_extract.py` → `classify_entity_type`, `annotate_entity_types` |
| **Role-edge extractor (Block 1.6)** ★ | `backend/entity_extract.py` → `extract_role_edges_from_raw_dir`, `merge_role_edges_into_graph` |
| Clusterer + labeler (Blocks 1.7 + 1.8) | `backend/graphify_runner.py` → `cluster_graph`, `_auto_label_communities` |
| Refinements (Blocks 2.1 + 2.2) | `backend/kb_corrections.py` |
| Router (Block 3.1) | `backend/graphify_runner.py` → `_route_to_entry_nodes` |
| Walker (Block 3.2) | `backend/graphify_runner.py` → `_bfs_subgraph` |
| Synthesizer + gap parser (Blocks 3.4 + 3.5) | `backend/graphify_runner.py` → `_synthesize_with_history`, `_parse_gaps`, `synthesize_answer` |
| Inference strategies (Block 3.6) | `backend/graphify_runner.py` → `_run_inference_strategy` |
| **Relations endpoint (Block 4.1)** ★ | `backend/main.py` → `entities_relations` |

★ = added in the last two shipping batches.
