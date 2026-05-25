# Intents — the smallest pluggable unit of behaviour

An **intent** is a named *angle* you attach to an LLM call. It's the one-
paragraph instruction that tells Claude *how to think about the question*
— separate from *what's in the graph* and separate from *the house style
of the answer*.

It's the smallest piece of pluggable behaviour in the whole system, and
it shows up everywhere a Claude call is made via the chat path.

---

## The three layers stacked on every LLM answer

Every conversation turn and every playbook intent-step composes its
prompt as:

```
SYSTEM PROMPT  =  House style rules   (locked in code, never changes)
               +  Intent               ← the lens / angle for THIS question
               +  Rubric               ← workspace-level evaluation rules
               +  Memory               ← durable user-asserted facts

USER PROMPT    =  Conversation history (turns or accumulated step outputs)
               +  Graph subgraph       (routed + walked)
               +  The actual question
```

**Intent ≠ rubric ≠ memory.** Easy to confuse, so:

| Piece | What it controls | Scope |
|---|---|---|
| **Intent** | The reasoning angle for *this* question ("explore", "red_team", "draft a PRD") | Per-turn, per-step |
| **Rubric** | Framing rules ("capital ceiling $X", "don't Sherlock partners", etc.) | Per-conversation / per-workspace |
| **Memory** | Facts you've told the brain to remember | Per-workspace, durable |
| **House style** | TL;DR, ≤22-word bullets, mandatory citations, gap block | Hard-coded in `graphify_runner.py` |

---

## The taxonomy

~50 built-in intents organised into 7 groups in `rubrics.py:246`
(`INTENT_GROUPS`).

### 1. Strategy / research

| ID | Label |
|---|---|
| `explore` | Explore / open-ended research |
| `product_idea` | Find a new product idea |
| `new_strategy` | Propose a new strategy |
| `modify_strategy` | Modify an existing strategy |
| `pivot` | Consider a pivot |
| `evaluate` | Evaluate / pressure-test an idea |
| `red_team` | Red-team — argue the opposite |
| `pre_mortem` | Pre-mortem — imagine it failed |
| `analogues` | Find analogues (history of similar bets) |
| `competitor_scan` | Competitor landscape scan |
| `customer_voice` | Voice of the customer |
| `synthesize` | Synthesize findings across documents |
| `find_gaps` | Find unexplored ideas (white space + bridges) |
| `moat_audit_present` | MOAT audit — present (graph-anchored) |
| `moat_audit_future` | MOAT audit — future paths (walk or skip per) |
| `path_to_win` | Find narrow paths to win against the odds |
| `external_scan` | Scan external signals the corpus doesn't cover |
| `audit_claims` | Audit KB — list load-bearing claims worth verifying |
| `verify_load_bearing` | Verify load-bearing claims against the live web |

### 2. Existing product / codebase

| ID | Label |
|---|---|
| `explain_codebase` | Explain this codebase |
| `review_code` | Review code quality |
| `plan_refactor` | Plan a refactor |
| `plan_feature` | Plan a feature addition |
| `security_audit` | Security audit |
| `plan_migration` | Plan a migration |
| `debug_issue` | Investigate a bug |

### 3. Product Manager

| ID | Label |
|---|---|
| `pm_spec` | Write a PRD / feature spec |
| `pm_user_stories` | Write user stories |
| `pm_risks` | Identify risks + mitigations |
| `pm_dependencies` | Map dependencies |
| `pm_prioritize` | Prioritize a backlog |
| `pm_build_buy_partner` | Decide build vs buy vs partner |
| `pm_metrics` | Define success metrics |

### 4. Engineering Manager

| ID | Label |
|---|---|
| `em_postmortem` | Run an incident postmortem |
| `em_capacity` | Plan team capacity |
| `em_interview` | Design an interview loop |
| `em_okrs` | Draft engineering OKRs |
| `em_dependency_audit` | Audit dependencies (outdated / CVE / over-permissioned) |
| `em_test_coverage` | Assess test coverage + gaps |
| `em_technical_feasibility` | Assess technical feasibility (cost / time / blockers) |

### 5. Growth

| ID | Label |
|---|---|
| `growth_experiment` | Find a growth experiment |
| `growth_funnel` | Diagnose a funnel drop |
| `growth_activation` | Plan an activation push |
| `growth_pricing` | Audit pricing & packaging |

### 6. Brownfield AI-led dev

These power the 7-step brownfield dev chain (Idea → PRD → ADR → Plan →
Delivery → Security → Tests).

| ID | Label |
|---|---|
| `bf_idea_refine` | Refine an idea against the existing codebase |
| `bf_prd_brownfield` | Write a PRD for a change to the existing codebase |
| `bf_architecture` | Design the architecture change (ADR) |
| `bf_planning` | Break the work into ordered, verifiable tasks |
| `bf_delivery` | Plan incremental delivery (TDD-first) |
| `bf_security` | Review security of the proposed change |
| `bf_test_plan` | Write the test plan |

### 7. Go-to-Market

| ID | Label |
|---|---|
| `gtm_icp` | Define the ICP |
| `gtm_positioning` | Build a positioning narrative |
| `gtm_pricing` | Recommend pricing + packaging |
| `gtm_channels` | Plan acquisition channels |
| `gtm_battlecard` | Build a competitive battlecard |
| `gtm_enablement` | Build a sales enablement kit |
| `gtm_beta` | Design a beta / design-partner program |
| `gtm_launch` | Plan a launch (T-30 / T-0 / T+30) |

---

## What an intent IS, mechanically

Just a string. `rubrics.intent_instruction("red_team", ws)` returns ~1
paragraph of plain text:

> *"The user wants the opposite case argued as strongly as possible. List
> the 3 strongest reasons this is wrong. End with the single signal that
> would change your mind…"*

That paragraph is **concatenated into the system prompt** of the LLM
call. That's the whole mechanism. No tool calls, no function dispatch,
no routing logic — just instruction text.

Some intents (like `find_gaps`, `moat_audit_present`) are 200–400 words
long and act more like mini-templates with strict output structure. But
the mechanism is identical — text in the system prompt.

---

## Where it gets applied

| Surface | How intent is selected |
|---|---|
| **Conversations tab** | You pick an intent when creating the conversation (or change it via the settings drawer). Every turn in that thread uses it until you change it. |
| **Playbook `intent_turn` steps** | Each step in a playbook hardcodes an intent (see `playbooks.py:58` and below — `"type": "intent_turn", "intent": "find_gaps"`, etc.). That's why a single playbook can chain `inventory → gaps → competitor_scan → red_team → pre_mortem`: each step is a different intent on the *same* growing scratchpad. |
| **Ask Graph** | **No intent applied** — uses a generic system prompt. By design — Ask Graph is for one-shot, quick lookups. |
| **ForeSight** | **No intent applied directly** — personas play the role intents play in Conversations. (A ForeSight session can optionally borrow an intent from a linked source conversation, used only by the final synthesizer.) |
| **Artifact follow-up Q&A** | Doesn't use an intent — uses an artifact-aware system prompt. |

---

## Where they live on disk

| Where | What |
|---|---|
| `backend/rubrics.py` — `INTENT_GROUPS`, `INTENT_LABELS`, `intent_instruction()` | Built-in registry. ~50 entries. Lives in code so the UI and backend can't drift. |
| `backend/intent_store.py` | User CRUD layer: per-workspace overrides + global user-defined intents |
| `backend/data/workspaces/<ws>/intents/` | JSON files for workspace-scoped overrides |
| `backend/data/global_intents/` | JSON files for cross-workspace user-defined intents |

---

## The override pattern

(Same shape as rubrics + playbooks + personas — see `CLAUDE.md` for the
convention.)

`intent_instruction(id, ws)` is the lookup function. Its precedence
(high → low):

1. **Workspace override** — `intent_store.get_intent(ws, id)` returns a non-empty body
2. **Global override** — user-created across all workspaces
3. **Built-in** — the hardcoded paragraph in `rubrics.py:367+`

When you edit a built-in intent in the UI, the system **materialises an
override** at the workspace scope (or global if you pick that), leaving
the built-in untouched in code. `restore-default` deletes the override →
built-in reappears.

Cloning a built-in into a new ID creates a fresh user-defined intent
(workspace or global scope) that the user can then edit freely without
affecting the original.

---

## What an intent does NOT change

This is the part people expect but isn't true:

- **Graph routing** — the router (LLM that picks entry nodes) sees the
  intent text but is not steered by it; routing is question-driven.
- **BFS walk** — completely deterministic, depth-3.
- **Citation rules** — house style enforces source_file citations
  regardless of intent.
- **Gap block** — gap analysis runs whether or not an intent is set.
- **Web grounding** — separate flag, intent doesn't enable/disable it.
- **Refinements overlay** — human edits always win, intent-independent.
- **Inference strategy** — `none` / `reflection` / `cove` / `best_of_3`
  is a conversation-level setting, applied after the intent shapes the
  draft.

So a `red_team` intent on a question about "Acme's pricing" still pulls
the same Acme-pricing subgraph and still cites the same source files —
but the *prose* will argue against the apparent pricing strategy
instead of explaining it.

---

## Quick example — same question, four intents

Question: *"Should we kill Project Phoenix?"*

| Intent | What you'd get |
|---|---|
| `explore` | "Three tensions worth surfacing: the team's confidence vs the corpus's revenue trend vs the customer voice. No verdict." |
| `evaluate` | "Three load-bearing assumptions: (1) market timing, (2) team availability, (3) distribution. Evidence supports (3), contradicts (1)." |
| `red_team` | "Kill it. Three reasons in order of weight…" (argues the opposite of what you wanted to hear) |
| `pre_mortem` | "Imagine it's 12 months later and we did kill it. What went wrong? What did we miss?" |

Same graph routing, same citations, same TL;DR-then-bullets shape.
Different angle.

---

## Why intents are powerful for Playbooks

A Playbook is a list of steps. Each `intent_turn` step picks one intent
from the registry. The runner threads the same scenario + growing
scratchpad through every step, but each step *reframes* the question
through its own intent.

Example — *Find Unexplored Ideas* (`playbooks.py:58`):

```
Step 1: inventory     (intent: synthesize)       ← "what's already here"
Step 2: gaps          (intent: find_gaps)        ← "what's NOT here"
Step 3: external      (intent: external_scan)    ← "what the corpus misses"
Step 4: competitors   (intent: competitor_scan)  ← "what others are doing"
Step 5: divergent     (intent: -, special type)  ← wild ideation pass
Step 6: synthesize    (final, structured JSON)   ← composes the brief
```

Each of those steps re-routes through the graph independently. Each
calls `rich_query` with a different `intent_instruction`. The same
scenario produces six different angles of analysis from the same
underlying corpus, in five LLM calls.

This is the bit that makes playbooks more than "a fancy prompt
template" — they're a curated *sequence of perspectives* on one
question.

---

## API surface for intents

| Endpoint | Purpose |
|---|---|
| `GET /api/intents` | List built-ins + user-defined, grouped for the UI |
| `GET /api/intents/source/{id}` | Return the full prompt body (built-in or override) |
| `GET /api/intents/custom` | List only user-defined intents |
| `POST /api/intents/custom` | Create a new user-defined intent |
| `PATCH /api/intents/custom/{id}` | Update or override a built-in |
| `DELETE /api/intents/custom/{id}` | Delete a user-defined intent or override |
| `POST /api/intents/{id}/restore-default` | Restore a built-in (deletes override) |
| `POST /api/intents/{id}/clone` | Clone any intent into a new ID |

All scoped by `X-Workspace-Id` header.

---

## TL;DR

An intent is a **named one-paragraph system-prompt enrichment** picked
from a registry of ~50, applied per-conversation-turn or per-playbook-
step. It steers *how* Claude reasons, not *what data* it sees. It's also
the unit that lets a Playbook chain together a complex strategy
workflow — each step is a different intent looking at the same
accumulating scratchpad of prior outputs.

Intents are deliberately the *cheapest possible* abstraction: plain text
in the system prompt. No control flow, no tools, no schema. That's why
they compose cleanly with everything else (rubric, memory, graph
routing, inference strategies, gap analysis) — they live in their own
slot and don't fight the other layers.

---

## Code map

| Concept | File · line |
|---|---|
| The taxonomy (`INTENT_GROUPS`, `INTENT_LABELS`) | `backend/rubrics.py:246` |
| The lookup function (override precedence) | `backend/rubrics.py:350` (`intent_instruction`) |
| Built-in instruction bodies | `backend/rubrics.py:367+` (the `mapping` dict) |
| User-defined / overridden intent CRUD | `backend/intent_store.py` |
| Conversation turn applying an intent | `backend/main.py` → `conversations_turn` → `rich_query` |
| Playbook step applying an intent | `backend/playbooks.py:953` (`_run_intent_step`) |
| The function that folds intent into the LLM call | `backend/graphify_runner.py:1473` (`rich_query`) → `_synthesize_with_history` |
| API endpoints | `backend/main.py` → `intents_*` handlers |
| UI: intent picker | `frontend/src/components/IntentSelect.tsx` |
| UI: intent library / editor | `frontend/src/components/IntentLibrary.tsx` |
