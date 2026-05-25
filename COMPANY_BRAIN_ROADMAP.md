# Company Brain — Goal, Plan, Roadmap

Version 1.0 · 2026-05-25

This document defines what we are building, why, and the step-by-step path from today's prototype to a credible "Company Brain" demo. It is scoped to **prototype work only** — connectors, ingestion at scale, and drift detection are deferred until product is approved.

---

## 1. Goal

Turn the current knowledge-graph prototype into a system that:

1. Captures how a company actually works — including tacit knowledge in people's heads, not just documents.
2. Represents that know-how as **typed, executable skills** with tool bindings, decision branches, and safety guards.
3. Lets AI agents discover and call those skills to do real work, safely and consistently.

The proof point: a non-technical SME describes a workflow in conversation → the brain turns it into a tested skill → an external AI agent calls it via API → the skill executes (dry-run against a mock tool) → every action is audited and citable back to the SME's input.

---

## 2. Today's baseline

The prototype already has:

- KB ingestion (upload), graph extraction, communities, god nodes, insights
- Conversations grounded in graph + memory + rubrics + inference strategies
- **Playbooks** — multi-step LLM workflows that produce typed Artifacts (OpportunityScan, PRDDraft, BuildBuyDecision, LaunchPlan, etc.)
- Artifact chaining, personas, simulate, refinements, workspaces

This is a strong **strategic thinking partner**. It is **not yet** a Company Brain because:

- Playbook outputs are markdown prose, not typed contracts agents can call.
- Nothing executes — the brain advises, it does not act.
- Knowledge only enters via documents; tacit "how we do it" lives nowhere.
- No safety, no audit, no confidence signals for action.

---

## 3. Gap summary

| # | Gap | One-line fix |
|---|-----|--------------|
| 1 | No skill schema | Typed manifest with inputs, outputs, branches, risk tier |
| 2 | No tool execution | Tool registry + step bindings + dry-run |
| 3 | No safety surface | Risk tiers, approval queue, audit log |
| 4 | No tacit-knowledge capture | SME interview mode + trace-to-skill drafter |
| 5 | No skill mining from graph | Candidate detector + SME review queue |
| 6 | No skill testing | Test cases, pass rate, sandbox runner |
| 7 | No operational ontology | Actor/trigger/precondition/action/postcondition overlay |
| 8 | No trust surface for callers | Citations, freshness, ask-a-human threshold |

---

## 4. Plan — phased, with acceptance criteria

Each phase ships behind a feature flag and is demoable on its own. No phase depends on a later phase being "done well" — each lands a vertical slice.

### Phase 0 — Foundation: Skill schema and registry (Gaps 1, 7)

**What to build**

- `Skill` data model in `backend/`:
  - `id`, `name`, `version`, `owner_sme`, `description`
  - `risk_tier`: `auto | approval | dual_approval`
  - `inputs`: typed JSON schema
  - `outputs`: typed JSON schema
  - `steps`: ordered list of `{ kind: llm | tool_call | branch | human_approval, ... }`
  - `branches`: condition → next step
  - `citations`: source nodes / conversations / SME turns
  - `freshness`: `last_verified_at`, `last_run_at`
- Operational overlay on the graph: nodes can be tagged as `actor | trigger | precondition | action | tool | postcondition | escalation`. Store as node metadata, not a new graph.
- REST: `GET /api/skills`, `GET /api/skills/{id}`, `POST /api/skills`, `PATCH /api/skills/{id}`.
- Persist as JSON files under `data/workspaces/<id>/skills/` to match existing storage style.

**Acceptance**

- A hand-authored skill JSON loads, validates, and is listed via API.
- One existing playbook (e.g. `discover_opportunity`) is re-expressed as a `Skill` manifest with no behavioral regression.
- Graph nodes can be tagged with operational roles via API or admin endpoint.

### Phase 1 — Action: tool registry and dry-run (Gap 2)

**What to build**

- `Tool` registry — registered functions or HTTP endpoints with:
  - `id`, `name`, `description`, `inputs schema`, `outputs schema`
  - `auth_scope` (string label, not real auth yet)
  - `mock`: optional canned response for dry-run
- Step kind `tool_call` resolves to a registered tool by id.
- `SkillRunner` service:
  - Executes steps sequentially.
  - For `tool_call`, calls the real tool **only** when `mode=live`. Default mode is `dry_run`, which uses the tool's mock.
  - Returns a full trace: each step's input, output, decision, latency.
- REST: `POST /api/skills/{id}/run` with `{ inputs, mode }`.
- Seed two mock tools: `mock_refund_api`, `mock_ticket_status`.

**Acceptance**

- A skill `issue_refund_under_50` can be defined that calls `mock_refund_api`.
- `POST /api/skills/issue_refund_under_50/run` with `mode=dry_run` returns a structured trace showing the mock call.
- Same call with `mode=live` is rejected unless explicitly allowed.

### Phase 2 — Safety: approvals and audit (Gap 3)

**What to build**

- Risk tier enforcement in `SkillRunner`:
  - `auto` — runs end-to-end.
  - `approval` — pauses at first `human_approval` step or before any `tool_call` if `live`.
  - `dual_approval` — requires two approvers.
- Approval queue:
  - Persist pending runs under `data/workspaces/<id>/approvals/`.
  - REST: `GET /api/approvals`, `POST /api/approvals/{run_id}/approve`, `POST /api/approvals/{run_id}/reject`.
- Audit log:
  - Every skill run writes an immutable record: inputs, mode, steps trace, tool calls, approvers, final output, errors.
  - REST: `GET /api/skills/{id}/runs`, `GET /api/runs/{run_id}`.
- Minimal frontend: an **Approvals** tab listing pending runs with approve / reject buttons.

**Acceptance**

- An `approval`-tier skill with `mode=live` pauses, surfaces in the Approvals tab, and only proceeds after a human clicks approve.
- Every run, approved or rejected, is queryable in the audit log.

### Phase 3 — Capture: SME interview mode (Gap 4)

**What to build**

- New conversation intent `sme_interview` with a system prompt that:
  - Asks targeted Socratic questions about a workflow ("What triggers this? What do you check first? What's the exception? When do you escalate?").
  - Maintains an in-conversation `SkillDraft` artifact, updated turn by turn.
- New step in playbook chain: `trace_to_skill_draft` — paste a Slack/ticket transcript, get a `SkillDraft` proposal.
- `SkillDraft` artifact type:
  - Same shape as `Skill` but `status=draft`, no version.
  - "Promote to Skill" action turns it into a versioned skill.
- Frontend: a **Skills** tab listing skills + drafts; "Start interview" button creates an `sme_interview` conversation pre-seeded with the workflow name.

**Acceptance**

- An SME can complete a 10-turn interview about "how we issue refunds" and end with a `SkillDraft` containing trigger, preconditions, action steps, and escalation.
- The draft is editable inline, then promoted to a versioned skill.

### Phase 4 — Mining: skill candidates from the graph (Gap 5)

**What to build**

- `SkillCandidateDetector` job:
  - Scans graph communities for procedural patterns (verbs, sequences, "if X then Y" phrasing in source text).
  - Uses the operational ontology overlay (Phase 0) — communities with `trigger` + `action` + `postcondition` nodes become candidates.
  - Outputs a ranked list with source citations.
- Review queue:
  - REST: `GET /api/skill-candidates`, `POST /api/skill-candidates/{id}/accept`, `POST /api/skill-candidates/{id}/reject`.
  - Accepting opens an `sme_interview` pre-seeded with the candidate.
- Frontend: a **Discovery** sub-view on the Skills tab listing candidates with TL;DR + source links.

**Acceptance**

- After running on the existing PDF corpus, the detector produces at least one plausible skill candidate (e.g. "Triage incoming product idea") with citations.
- Accepting a candidate creates an interview conversation; the SME confirms or edits.

### Phase 5 — Trust: testing and confidence (Gaps 6, 8)

**What to build**

- Test cases per skill:
  - `Skill.test_cases`: list of `{ inputs, expected_outputs | expected_branch }`.
  - `POST /api/skills/{id}/test` runs all cases in dry-run, returns pass/fail per case.
- Confidence score:
  - `confidence = pass_rate × freshness_factor × sme_signoff_factor`.
  - Surfaced on every skill and in the registry list endpoint.
- "Ask a human" threshold:
  - Skills declare `min_confidence`. Callers below threshold get `status=needs_human` instead of execution.
- Citations:
  - Every skill step carries `cited_from`: list of graph node ids / conversation turn ids / SME interview ids.
  - Surfaced in the run trace and the Skills tab.

**Acceptance**

- A skill with three test cases shows a real pass rate badge.
- An external caller below `min_confidence` receives a structured "needs human review" response, not an execution.
- Every step in a run trace links back to a source artifact.

### Phase 6 — External agent loop (the demo)

**What to build**

- Public-facing skill discovery + invocation:
  - `GET /api/v1/skills` — list (id, name, description, inputs schema, outputs schema, confidence, risk tier).
  - `POST /api/v1/skills/{id}/invoke` — typed input, returns run result or approval-pending handle.
- Single mock SDK snippet (Python, 20 lines) showing an external agent:
  1. List skills.
  2. Find one that matches its intent.
  3. Invoke with typed inputs.
  4. Handle `needs_human` and `pending_approval` responses.

**Acceptance**

- A throwaway Python script, running outside the app, completes the loop: list → pick → invoke → receive audited result.
- The full chain is visible in the app: external caller → run trace → approvals → audit log → citations back to the SME interview that authored the skill.

---

## 5. Roadmap

Single-builder velocity assumed. Each phase is 1–2 weeks; ship behind a flag, demo, iterate.

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1 | Phase 0 | Skill schema, registry API, one playbook re-expressed as a skill |
| 2 | Phase 1 | Tool registry, dry-run runner, two mock tools, one runnable skill |
| 3 | Phase 2 | Risk tiers, approval queue, audit log, Approvals tab |
| 4 | Phase 3 | SME interview intent, `SkillDraft` artifact, Skills tab |
| 5 | Phase 3 | Trace-to-skill drafter, promote-to-skill flow |
| 6 | Phase 4 | Candidate detector, review queue, Discovery view |
| 7 | Phase 5 | Test cases, confidence scores, citations, freshness |
| 8 | Phase 6 | Public skill API, external caller demo, full audit loop |

**Demoable checkpoints**

- End of week 2 — "Look, a typed skill runs and produces a trace."
- End of week 4 — "An SME just authored a real skill in 10 minutes of conversation."
- End of week 6 — "The brain proposed skills the SME hadn't thought to write down."
- End of week 8 — "An external AI agent just did the work, safely, end-to-end."

---

## 6. The demo arc (rehearse this from week 1)

The story we want a YC partner / pilot customer to see in ten minutes:

1. **Open the app cold.** No skills exist for "issue refund."
2. **Start an SME interview.** The brain asks the support lead five sharp questions. A `SkillDraft` builds turn by turn on screen.
3. **Promote the draft.** Add two test cases. Pass rate goes green.
4. **Open the Discovery view.** The brain has already proposed two adjacent skills mined from existing support docs — "handle chargeback dispute," "escalate VIP refund." Accept one for later interview.
5. **Switch to a terminal.** Run a 20-line Python script: a fake "support agent" lists skills, picks `issue_refund_under_50`, invokes it.
6. **The approval pings in the app.** Approve. The mock refund API is called. The trace shows every step, every citation, every approver.
7. **Show the audit log.** Every action is provable back to a specific SME turn from step 2.

This is the demo. Every phase above is in service of it.

---

## 7. What we are explicitly not building (yet)

These are real and important. They are deferred until product is approved and we have a pilot customer pulling on them:

- Connectors (Slack, Gmail, Zendesk, Jira, Salesforce, etc.)
- Industrial ingestion + incremental sync + change detection
- Drift detection and skill re-derivation when sources change
- Multi-tenant auth, SSO, SCIM, RBAC
- SOC 2 / data residency / retention controls
- Real-world tool integrations (Stripe, Zendesk actions, etc. — only mocks during prototype)
- Embedding / vector search over the corpus
- Bulk skill execution, scheduling, batching

The prototype proves the **shape** of the product. Production-grade I/O comes after the shape is validated.

---

## 8. Risks and likely cuts

| Risk | Mitigation / cut |
|------|------------------|
| Skill schema becomes a research project | Time-box Phase 0 to one week. Pick a shape, ship, refactor in Phase 5 if needed. |
| SME interview produces shallow skills | Add a pre-built question bank per workflow archetype (support, ops, incident) in Phase 3. |
| Candidate detector finds nothing useful | Acceptable. The interview path alone proves the product. Detector is bonus signal, not core. |
| Confidence math turns into a rabbit hole | Use a deliberately crude formula in Phase 5. Improve only if a pilot asks for it. |
| Tool integration creep | Hard rule: only mocks during prototype. No real third-party API calls. |
| UI work dominates | Reuse existing Playbook / Artifact / Conversation surfaces wherever possible. Skills tab borrows the Playbooks tab layout. |

---

## 9. Single-sentence summary

Take the current strategy-doc thinking partner, layer **typed skills + tool execution + safety + tacit-knowledge capture** on top, and prove the loop end-to-end with a non-technical SME authoring a skill that an external agent then calls — in eight weeks.
