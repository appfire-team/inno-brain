# Idea Inbox — Feature Proposal

## Use case

People across the company submit ideas as PDFs. We want to test each one for "is it worth building?" and chain the verdict into the next step (PRD, build/buy decision, pre-mortem, kill).

## Today's friction

Per-PDF workflow is manual:
1. Upload the PDF under Documents (~60s graph extraction).
2. Open `Discover product opportunities` playbook.
3. Distill the PDF into a scenario sentence (or pick one of the auto-suggested chips if the PDF dominates a KB community).
4. Run the playbook, read the OpportunityScan, decide.

At 10+ ideas/month the human-in-the-loop steps (2 and 3) become the bottleneck.

## Proposal: an Ideas tab

Uploading a PDF tagged as an idea-submission auto-triages it:

1. Ingest the PDF (existing flow — graph extraction).
2. Once extraction completes, LLM-extract a one-paragraph forward-looking scenario from the PDF (use the workspace's default model).
3. Auto-spawn a `Discover product opportunities` playbook run with that scenario pre-seeded.
4. The result lands in an **Ideas** tab as a card showing OpportunityScan TL;DR + highlight chips + status pill.
5. One-click actions on each card promote the idea to the next playbook in the chain.

## Workflow this enables

- **Capture**: anyone drops a PDF in the Ideas inbox (or future: Slack `/idea` command, email-to-inbox).
- **Triage**: backend auto-runs `discover_opportunity` — ~5 min, async.
- **Review**: a human reads the OpportunityScan TL;DR + highlight strip in ~30 seconds and clicks:
  - 🎯 **Promote to PRD** → spawns `draft_prd` with this OpportunityScan as the source artifact
  - ⚖️ **Promote to Build/Buy/Partner** → spawns `build_buy_partner`
  - 🚨 **Stress-test first** → spawns `premortem_plan`
  - ✕ **Archive** → moves out of the active inbox

## Implementation sketch (~2-3 hours)

### Backend

- **Upload**: add `is_idea_submission: bool` param to the existing `/api/upload` endpoint (or a new `/api/ideas/submit` endpoint that wraps it).
- **Graph-extraction-complete hook**: when a flagged PDF finishes extraction, call a new helper `synthesize_idea_scenario(ws, file_id)`:
  - Reads the PDF text (already extracted during ingestion).
  - Asks the LLM for a one-paragraph forward-looking scenario framed as a debatable proposition.
  - Uses the workspace's default `answer_model`.
  - Cached (no need to re-synthesize on retry).
- **Auto-spawn**: trigger `playbooks.start_run(ws, "discover_opportunity", scenario=...)` immediately after synthesis.
- **Idea record**: persist `{ file_id, submitter, scenario, playbook_run_id, status, created_at }` under `data/workspaces/<id>/ideas/`.
- **List endpoint**: `GET /api/ideas` returns idea records joined with their OpportunityScan artifact summary.
- **Action endpoints**: `POST /api/ideas/{id}/promote` with `{ target: "prd" | "build_buy" | "premortem" | "archive" }` chains the next playbook.

### Frontend

- **New "Ideas" tab** in the main tab strip (between Conversations and Playbooks, or merged into Documents as a sub-view).
- **List view**: each idea = card with:
  - Submitter (from upload context or PDF metadata)
  - Original PDF title + link
  - OpportunityScan TL;DR + 3-5 highlight chips (reuses the magazine layout pieces already built)
  - Status pill: `triaging…` / `ready for review` / `promoted` / `archived`
  - Action buttons (promote / archive)
- **Card → Artifact link**: clicking the card opens the full OpportunityScan in the existing PlaybookRunView.

## Tradeoffs

- **Cost**: each PDF triggers one LLM scenario synthesis (~$0.01) + one full `discover_opportunity` run (~$0.10-0.50). At 50 ideas/month → $5-25 total. Acceptable.
- **Latency**: triage runs async; the card shows `triaging…` for ~5 min, then flips to `ready for review`. No UI blocking.
- **False signal**: the LLM-extracted scenario may miss the submitter's actual angle. Mitigation: the scenario is editable; clicking the card lets you tweak it and re-run.
- **Scope creep**: deliberately starting without inbound channels (Slack, email). Web upload + tab is the MVP.

## Future expansions (post-MVP)

- Inbound channels: Slack `/idea` slash command, email-to-ideas-inbox alias, browser extension for capturing web articles.
- Submitter attribution + threading (each idea owner sees a status feed).
- "Idea quality score" rubric to auto-rank the inbox.
- Notification when triage finishes (Slack DM to submitter, or email).
- Bulk actions (kill all archived ideas, re-run the inbox against a new rubric).

## Why this fits the product

- Reuses every existing primitive: Documents ingestion, graph extraction, playbook runs, artifact chaining, magazine-layout rendering.
- Closes the gap between "we accept ideas from anywhere" and "we systematically test them."
- Each artifact remains citable, refinable, and chainable — the inbox doesn't bypass the compounding-knowledge model, it accelerates entry into it.
