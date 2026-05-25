"""Reusable evaluation rubrics that shape how the Conversations LLM answers.

A rubric is a piece of framing text — usually company-specific constraints,
evaluation criteria, or stylistic rules — that gets folded into the system
prompt for every turn in a conversation that references it.

Built-ins live in `DEFAULT_RUBRICS` below (code, not config) so the UI and
backend stay aligned. User rubrics — and user overrides of built-ins — live
per-workspace at `data/workspaces/<id>/rubrics/*.json`. An override is just a
stored file whose id matches a built-in's id; the override wins on read.
Deleting an override restores the built-in (the canonical body is still in
code). Each workspace owns its own rubric set; new workspaces may be seeded
with snapshots of rubrics from another workspace at creation time.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from workspaces import Workspace

DEFAULT_APPFIRE_RUBRIC = """\
Apply these Appfire-specific evaluation rules when answering:

1. **Capital constraints.** Appfire is a ~$200M ARR Atlassian Marketplace vendor with channel-first
   acquisition DNA. Reject ideas that require >$200M of capital up-front. Prefer Motion B+ (small
   anchor acquisition + senior hires + organic build, $75–120M over 30 months).

2. **Sherlocking test.** Atlassian has absorbed prior Marketplace vendors (Code Barrel, Mindville,
   Halp, Opsgenie). If the idea is a feature Atlassian could bundle for free into Rovo/Compass/JSM,
   call out the Sherlocking risk explicitly.

3. **Distribution leverage.** Favor ideas that pull through existing Appfire assets — JMWE,
   BigPicture, Comala, Pluralsight Flow — and ride the 200K+ Jira/Confluence customer rail.

4. **Regulatory tailwinds.** Treat hard regulatory deadlines (FDA QMSR Feb 2 2026, EU AI Act
   Aug 2 2026, CMMC DFARS Nov 10 2025, Opsgenie EOL Apr 5 2027) as forced-migration tailwinds —
   they're the strongest market drivers in the corpus.

5. **Audit honesty.** When the knowledge graph supports a claim, cite the source_file. When you're
   using general knowledge or reasoning beyond what the graph says, flag it as 'general knowledge'.

6. **Decisiveness.** Prefer concrete recommendations with rough ARR / cost / timing over
   generic options-thinking. Cite the named bets (Bet 1/2/3) and concrete dollar ranges when
   the corpus has them."""


# Canonical built-in rubrics. Keys are stable IDs that may be referenced from
# conversations or playbooks; the UI surfaces them as `source: "builtin"`.
DEFAULT_RUBRICS: dict[str, dict[str, str]] = {
    "appfire_default": {
        "id": "appfire_default",
        "name": "Appfire context (default)",
        "body": DEFAULT_APPFIRE_RUBRIC,
    },
}


def _path(ws: Workspace, rid: str) -> Path:
    safe = "".join(c for c in rid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid rubric id")
    return ws.rubrics_dir / f"{safe}.json"


def _save(ws: Workspace, r: dict[str, Any]) -> None:
    ws.ensure_dirs()
    _path(ws, r["id"]).write_text(json.dumps(r, indent=2))


def _builtin(rid: str) -> dict[str, Any] | None:
    src = DEFAULT_RUBRICS.get(rid)
    if not src:
        return None
    return {**src}


def _override_exists(ws: Workspace, rid: str) -> bool:
    return rid in DEFAULT_RUBRICS and _path(ws, rid).exists()


def _annotate(ws: Workspace, r: dict[str, Any]) -> dict[str, Any]:
    """Attach the source discriminator used by the frontend."""
    rid = r["id"]
    if rid in DEFAULT_RUBRICS:
        r["source"] = "customized" if _path(ws, rid).exists() else "builtin"
    else:
        r["source"] = "user"
    return r


def list_rubrics(ws: Workspace) -> list[dict[str, Any]]:
    """Merge built-ins with the workspace's stored rubrics. Stored records
    whose id matches a built-in's id override the built-in's name/body but
    keep the source tagged as 'customized' so the UI can offer Restore."""
    by_id: dict[str, dict[str, Any]] = {}
    for rid, body in DEFAULT_RUBRICS.items():
        by_id[rid] = {**body}
    if ws.rubrics_dir.exists():
        for p in sorted(ws.rubrics_dir.glob("*.json")):
            try:
                r = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            by_id[r["id"]] = r
    # Sort: built-ins first (in their declared order), then user rubrics alphabetically.
    builtin_ids = list(DEFAULT_RUBRICS.keys())
    builtins = [_annotate(ws, by_id[rid]) for rid in builtin_ids if rid in by_id]
    user = sorted(
        (_annotate(ws, r) for rid, r in by_id.items() if rid not in DEFAULT_RUBRICS),
        key=lambda r: r.get("name", "").lower(),
    )
    return [*builtins, *user]


def get_rubric(ws: Workspace, rid: str) -> dict[str, Any] | None:
    """Workspace override wins; otherwise fall through to the built-in registry."""
    p = _path(ws, rid)
    if p.exists():
        return _annotate(ws, json.loads(p.read_text()))
    builtin = _builtin(rid)
    if builtin:
        return _annotate(ws, builtin)
    return None


def create_rubric(ws: Workspace, name: str, body: str) -> dict[str, Any]:
    now = time.time()
    r = {
        "id": uuid.uuid4().hex[:10],
        "name": name.strip() or "Untitled rubric",
        "body": body,
        "created_at": now,
        "updated_at": now,
    }
    _save(ws, r)
    return _annotate(ws, r)


def update_rubric(ws: Workspace, rid: str, name: str | None = None, body: str | None = None) -> dict[str, Any] | None:
    """Patch an existing user rubric in this workspace, OR write the first
    override for a built-in.

    The first save against a built-in id materializes the override file from
    the canonical built-in body before applying the patch — so the user can
    edit just the fields they care about and the rest stays in sync."""
    p = _path(ws, rid)
    if p.exists():
        r = json.loads(p.read_text())
    elif rid in DEFAULT_RUBRICS:
        base = DEFAULT_RUBRICS[rid]
        now = time.time()
        r = {
            "id": rid,
            "name": base["name"],
            "body": base["body"],
            "created_at": now,
            "updated_at": now,
        }
    else:
        return None
    if name is not None:
        r["name"] = name.strip() or r["name"]
    if body is not None:
        r["body"] = body
    r["updated_at"] = time.time()
    _save(ws, r)
    return _annotate(ws, r)


def delete_rubric(ws: Workspace, rid: str) -> bool:
    """Delete a user rubric in this workspace, OR remove the override for a
    built-in (which restores the canonical body — the built-in itself isn't
    deletable)."""
    p = _path(ws, rid)
    if not p.exists():
        return False
    p.unlink()
    return True


def restore_default_rubric(ws: Workspace, rid: str) -> dict[str, Any] | None:
    """Drop the workspace's override for a built-in and return the canonical
    version. Returns None if `rid` is not a built-in."""
    if rid not in DEFAULT_RUBRICS:
        return None
    p = _path(ws, rid)
    if p.exists():
        p.unlink()
    return _annotate(ws, _builtin(rid) or {})


def list_available_rubrics_for_picker() -> list[dict[str, Any]]:
    """For the create-workspace UI: enumerate all rubrics the user could copy
    into a new workspace — built-in templates + all rubrics across all
    existing workspaces. Each entry has {id, name, body, workspace_id,
    workspace_name, source}. Used to populate the rubric picker."""
    import workspaces as ws_store
    out: list[dict[str, Any]] = []
    # Built-ins first.
    for rid, base in DEFAULT_RUBRICS.items():
        out.append({
            "id": rid,
            "name": base["name"],
            "body": base["body"],
            "source": "builtin",
            "workspace_id": None,
            "workspace_name": None,
        })
    # Then a row per (workspace, rubric) pair for any user/customized rubrics.
    for ws_summary in ws_store.list_workspaces():
        ws = ws_store.get_workspace(ws_summary["id"])
        if ws is None or not ws.rubrics_dir.exists():
            continue
        for p in sorted(ws.rubrics_dir.glob("*.json")):
            try:
                r = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            out.append({
                "id": r.get("id"),
                "name": r.get("name") or "Untitled rubric",
                "body": r.get("body") or "",
                "source": "customized" if r.get("id") in DEFAULT_RUBRICS else "user",
                "workspace_id": ws.id,
                "workspace_name": ws.name,
            })
    return out


def seed_defaults() -> None:
    """No-op kept for backwards-compatibility with main.py's startup hook.

    Built-ins now live in code (`DEFAULT_RUBRICS`) and are merged at read time,
    so there's nothing to write to disk on fresh installs."""
    return


# Intent labels are fixed — they live in code not config so the UI and backend stay aligned.
# Defined in groups so the UI can render <optgroup>s. INTENT_LABELS is derived for the flat
# lookup paths that still want id → label.
INTENT_GROUPS: list[dict[str, Any]] = [
    {
        "label": "Strategy / research",
        "intents": {
            "explore": "Explore / open-ended research",
            "product_idea": "Find a new product idea",
            "new_strategy": "Propose a new strategy",
            "modify_strategy": "Modify an existing strategy",
            "pivot": "Consider a pivot",
            "evaluate": "Evaluate / pressure-test an idea",
            "red_team": "Red-team — argue the opposite",
            "pre_mortem": "Pre-mortem — imagine it failed",
            "analogues": "Find analogues (history of similar bets)",
            "competitor_scan": "Competitor landscape scan",
            "customer_voice": "Voice of the customer",
            "synthesize": "Synthesize findings across documents",
            "find_gaps": "Find unexplored ideas (white space + bridges)",
            "moat_audit_present": "MOAT audit — present (graph-anchored)",
            "moat_audit_future": "MOAT audit — future paths (walk or skip per)",
            "path_to_win": "Find narrow paths to win against the odds",
            "external_scan": "Scan external signals the corpus doesn't cover",
            "audit_claims": "Audit KB — list load-bearing claims worth verifying",
            "verify_load_bearing": "Verify load-bearing claims against the live web",
        },
    },
    {
        "label": "Existing product / codebase",
        "intents": {
            "explain_codebase": "Explain this codebase",
            "review_code": "Review code quality",
            "plan_refactor": "Plan a refactor",
            "plan_feature": "Plan a feature addition",
            "security_audit": "Security audit",
            "plan_migration": "Plan a migration",
            "debug_issue": "Investigate a bug",
        },
    },
    {
        "label": "Product Manager",
        "intents": {
            "pm_spec": "Write a PRD / feature spec",
            "pm_user_stories": "Write user stories",
            "pm_risks": "Identify risks + mitigations",
            "pm_dependencies": "Map dependencies",
            "pm_prioritize": "Prioritize a backlog",
            "pm_build_buy_partner": "Decide build vs buy vs partner",
            "pm_metrics": "Define success metrics",
        },
    },
    {
        "label": "Engineering Manager",
        "intents": {
            "em_postmortem": "Run an incident postmortem",
            "em_capacity": "Plan team capacity",
            "em_interview": "Design an interview loop",
            "em_okrs": "Draft engineering OKRs",
            "em_dependency_audit": "Audit dependencies (outdated / CVE / over-permissioned)",
            "em_test_coverage": "Assess test coverage + gaps",
            "em_technical_feasibility": "Assess technical feasibility (cost / time / blockers)",
        },
    },
    {
        "label": "Growth",
        "intents": {
            "growth_experiment": "Find a growth experiment",
            "growth_funnel": "Diagnose a funnel drop",
            "growth_activation": "Plan an activation push",
            "growth_pricing": "Audit pricing & packaging",
        },
    },
    {
        "label": "Brownfield AI-led dev",
        "intents": {
            "bf_idea_refine": "Refine an idea against the existing codebase",
            "bf_prd_brownfield": "Write a PRD for a change to the existing codebase",
            "bf_architecture": "Design the architecture change (ADR)",
            "bf_planning": "Break the work into ordered, verifiable tasks",
            "bf_delivery": "Plan incremental delivery (TDD-first)",
            "bf_security": "Review security of the proposed change",
            "bf_test_plan": "Write the test plan",
        },
    },
    {
        "label": "Go-to-Market",
        "intents": {
            "gtm_icp": "Define the ICP",
            "gtm_positioning": "Build a positioning narrative",
            "gtm_pricing": "Recommend pricing + packaging",
            "gtm_channels": "Plan acquisition channels",
            "gtm_battlecard": "Build a competitive battlecard",
            "gtm_enablement": "Build a sales enablement kit",
            "gtm_beta": "Design a beta / design-partner program",
            "gtm_launch": "Plan a launch (T-30 / T-0 / T+30)",
        },
    },
]

INTENT_LABELS: dict[str, str] = {
    intent_id: label
    for group in INTENT_GROUPS
    for intent_id, label in group["intents"].items()
}


def intent_instruction(intent: str | None, ws: Any = None) -> str:
    """Return a one-paragraph LLM instruction tailored to the intent.

    Lookup order: user override (workspace, then global) > built-in. A user
    record whose id matches a built-in's id acts as an override and wins.
    `ws` is optional — if omitted, only built-ins are consulted (used by code
    paths that don't have a workspace handle yet).
    """
    # Check for a user override first so customized built-ins take effect.
    if ws is not None and intent:
        try:
            import intent_store  # local import to avoid load-time cycle
            user_intent = intent_store.get_intent(ws, intent)
            if user_intent and user_intent.get("body"):
                return user_intent["body"]
        except Exception:
            pass
    mapping = {
        "explore": "The user is doing open-ended exploration. Surface non-obvious connections and "
                   "interesting tensions in the corpus. Don't force a conclusion if the question is open.",
        "product_idea": "The user is hunting for a new product opportunity. For each candidate, give a "
                        "one-line description, estimated ARR range, target buyer persona, and the single "
                        "biggest risk. Prefer candidates the corpus supports.",
        "new_strategy": "The user wants a fresh strategic recommendation. Be opinionated. Propose a "
                        "primary bet plus a contingency. Explain why this bet, not others. Include rough "
                        "investment size, time horizon, and a leading indicator the user can watch.",
        "modify_strategy": "The user is iterating on an existing strategy. Identify what changed, what "
                           "to keep, what to drop, and what to test next. Reference the prior strategy's "
                           "structure explicitly.",
        "pivot": "The user is weighing a pivot. List the strongest argument FOR the pivot and the "
                 "strongest argument AGAINST it. End with a clear go/no-go recommendation and the "
                 "cheapest experiment that would falsify the wrong choice.",
        "evaluate": "The user is pressure-testing an idea. Identify the load-bearing assumptions, the "
                    "ways it could fail, and the evidence in the corpus that supports or contradicts it. "
                    "Be skeptical without being dismissive.",
        "synthesize": "The user wants a synthesis across multiple documents. Identify convergent claims "
                      "(things multiple papers agree on), divergent claims (where they disagree), and "
                      "claims that appear in only one source.",
        "find_gaps": "The user wants to surface unexplored ideas — adjacent to what's already in this "
                     "corpus but not yet covered. Structure the answer in four parts: "
                     "(1) Inventory — 5-8 dominant themes already present, one line each, with rough density "
                     "of corpus evidence (sparse / well-covered / saturated). "
                     "(2) Internal white space — topics that the corpus's audience would care about but the "
                     "corpus is silent on. Be specific: name the gap, not 'more research needed'. "
                     "(3) Bridges — pairs of disconnected themes whose combination no single document proposes. "
                     "Each bridge: one-line description, why it's interesting, and the precondition that "
                     "would make it viable. "
                     "(4) Candidate unexplored ideas — 5-10, grouped by gap category. For each: one-line "
                     "description, which existing theme it extends (so adjacency is explicit), the single "
                     "most load-bearing assumption, and the cheapest experiment that would falsify it. "
                     "Bias toward ideas the corpus's evidence base could be extended to support, not "
                     "fantastical pivots. When the graph is silent on a topic, flag 'graph silent'.",
        "moat_audit_present": (
            "Audit the MOATs that EXIST TODAY for this product/company. Anchor every "
            "claim in named entities from the knowledge graph — products, customers, "
            "channels, partnerships, regulations, data assets, named teams. The graph "
            "subgraph in your context is the source of truth; cite source_file for "
            "every load-bearing claim.\n\n"
            "Cover all 5 moat layers — none may be skipped. For each layer, output:\n"
            "  • STRENGTH (1-5, with 1 = essentially none, 5 = structural and durable)\n"
            "  • DECAY RATE: fast (< 12 mo) / medium (1-3 yr) / slow (3+ yr)\n"
            "  • VERDICT (one word): REAL (defensible, hard to copy) / THIN (exists but "
            "    weakly defensible — competitor could neutralise in 12 mo) / ILLUSION "
            "    (the corpus or pitch claims a moat but the evidence doesn't support it; "
            "    rename it as a feature, not a moat).\n"
            "  • SINGLE WEAKNESS (the one thing that makes this moat thinner than it looks)\n"
            "  • EVIDENCE: 2-4 graph entities (by name) that support the strength rating; "
            "    cite source_file. If the graph is silent on this layer, say "
            "    'graph-silent — relying on general knowledge' explicitly.\n\n"
            "The 5 layers:\n"
            "  1. PRODUCT moat — tech / IP / UX / switching cost embedded in the product\n"
            "  2. COMPANY moat — talent depth, capital, brand, governance, org velocity\n"
            "  3. DISTRIBUTION moat — channels, embed surface, partnerships, default-position\n"
            "  4. DATA moat — proprietary data accumulated per customer / model trained on it\n"
            "  5. NETWORK moat — cross-side effects, marketplace dynamics, ecosystem lock-in\n\n"
            "End with: 'STRONGEST moat today: [layer] — [one sentence why]. WEAKEST: "
            "[layer] — [the structural reason it's weak].' Be opinionated; rate honestly. "
            "A 3 is a 3, even if it's the strongest layer present."
        ),
        "moat_audit_future": (
            "Assess MOATs that COULD be built over the next 12-24 months. Surface 4-8 "
            "concrete candidate moat paths grounded in the knowledge graph where the "
            "corpus speaks; mark each as 'graph-anchored' (entities exist) or "
            "'graph-silent' (general knowledge or external).\n\n"
            "For EACH candidate moat path, output exactly this structure:\n"
            "  • WHAT TO BUILD: one concrete sentence — name the artifact, integration, "
            "    or motion (e.g. 'Pluralsight Flow connector that pulls per-team velocity "
            "    data into Resolver's recommendation engine').\n"
            "  • MOAT TYPE UNLOCKED: which of the 5 layers (Product / Company / "
            "    Distribution / Data / Network) — or which existing moat this extends.\n"
            "  • TIME TO DEFENSIBILITY: months until a competitor can no longer easily copy "
            "    (be honest — most paths take 18-36 months; flag 'no defensibility window' "
            "    explicitly if the model judges this is feature-not-moat).\n"
            "  • LOAD-BEARING ASSUMPTION: the single thing that has to be true (e.g. "
            "    'Atlassian doesn't bundle this into Rovo in <12 months').\n"
            "  • CHEAPEST VALIDATION: the smallest, fastest experiment that proves or "
            "    falsifies the load-bearing assumption (target: <$50K, <90 days).\n"
            "  • WALK or SKIP verdict + one-line rationale. WALK only when ALL three "
            "    are true: defensibility window > 12 months, validation < $50K, and at "
            "    least one named company asset (channel, product, customer base) is "
            "    leverageable. Otherwise SKIP.\n\n"
            "Rank the WALK paths in priority order at the end. The final line must be: "
            "'TOP PATH: [name] — [one sentence on why it's the path most worth walking].' "
            "Do not silently drop SKIP paths — surface them with the verdict so the human "
            "reviewer sees what was considered and rejected, not just the survivors."
        ),
        "path_to_win": (
            "The user wants the disciplined contrarian search — narrow paths to win "
            "in spite of the skeptical upstream analysis. Read everything before "
            "this step: the SKIPs, the KILLs, the red-team's killing blows, the "
            "pre-mortem's failure modes, the rubric's KILL biases. They are all "
            "PROBABLY right. Your job is NOT to advocate for the rejected ideas — "
            "your job is to find the small openings where, in spite of valid "
            "pushback, a credible path exists. Where can we thread the needle?\n\n"
            "Surface 3-5 paths. Quality over breadth. For EACH path output exactly "
            "this structure:\n"
            "  • THE WEDGE: the single narrowest opening — name a specific customer "
            "    segment, regulatory deadline, channel, partnership, competitor "
            "    blind spot, or buyer persona where the standard objection does "
            "    NOT apply. 'A bookshop in Vermont before Amazon noticed' beats "
            "    'the SMB segment'. Be specific.\n"
            "  • UNFAIR ADVANTAGE WE'D NEED TO MANUFACTURE: not what we have today. "
            "    What we'd need to BUILD or BORROW (named partner, hired person, "
            "    data set, regulatory cert, exclusive content, founder access) "
            "    to make this wedge real. State the cost (dollars + months) honestly.\n"
            "  • CONTRARIAN MOVE: the thing competitors won't do, can't do, or "
            "    are too big/structurally-conflicted to do. Why don't they do it? "
            "    (Sherlocking risk, ideological commitment, channel conflict, "
            "    regulatory exposure, org distraction, board pressure.) The "
            "    reason must be structural — 'they just haven't thought of it' "
            "    is not enough.\n"
            "  • 30/60/90-DAY PROOF: the single cheapest experiment that flips "
            "    the verdict from SKIP to WALK. Cost cap < $50K. If it succeeds, "
            "    name the next narrowest wedge after this one.\n"
            "  • ASYMMETRY CHECK: bounded downside? (How bad if it fails — "
            "    dollars + months wasted.) 10x+ upside? (What the world looks "
            "    like if it works.) Both must be named, not assumed.\n"
            "  • HONEST PROBABILITY: 'tail bet (<5%)' / 'long shot (5-20%)' / "
            "    'real chance (20-50%)'. Anything above 50% means you're not "
            "    being honest about why the upstream analysis flagged it. The "
            "    point of this exercise is the contrarian search, not "
            "    over-correction.\n\n"
            "Rules for output:\n"
            "- DO NOT contradict the upstream skeptical analysis. These paths "
            "  are 'in spite of' not 'instead of'. The rubric's KILL biases "
            "  remain in force; this section is the audit trail of what was "
            "  considered, where the openings live, and what would have to be "
            "  true to take them.\n"
            "- Name specific companies, customers, regulations, dates, dollar "
            "  amounts. Vague paths don't count and waste the user's time.\n"
            "- Prefer wedges that exploit asymmetry (small play, large optionality) "
            "  over heroic full-strategy pivots. We are looking for the foot in "
            "  the door, not the door itself.\n"
            "- End with one final line: 'THE PATH I'D STAKE THE MOST ON: <name> — "
            "  <one sentence on why it threads the needle when others can't>.' "
            "  This is the single contrarian bet — even if every prior step said "
            "  no, this is the one that's worth at least the 30-day proof."
        ),
        "audit_claims": "The user wants to audit which corpus claims are most likely to be wrong NOW. "
                        "Scan the subgraph for load-bearing claims and pick the 15-25 most worth re-verifying. "
                        "Bias the list toward claims that decay: (a) specific numbers (ARR, headcount, market size, "
                        "valuations, percentages), (b) dated facts (regulatory deadlines, product release dates, "
                        "earnings periods), (c) named status assertions (X is the market leader, Y is in beta), "
                        "(d) staff/leadership names, (e) competitive positions, (f) pricing. Skip generic strategic "
                        "claims that don't decay. For each pick: precise claim wording (verbatim if you can), the "
                        "source_file it came from, why it matters (downstream conclusions that depend on it), and "
                        "the cheapest signal that would falsify or confirm it. Group as: CRITICAL (load-bearing for "
                        "current strategy) / IMPORTANT / WORTH-CHECKING. End with the single claim most likely to "
                        "have moved since the corpus was compiled.",
        "verify_load_bearing": "The user has a list of corpus claims and wants the live web's current view on each. "
                               "For every CRITICAL and IMPORTANT claim from the audit list, web-search for the most "
                               "recent authoritative source (last 6 months preferred). Output a table-shaped list — "
                               "one row per claim — with columns: (1) the corpus claim verbatim, (2) STATUS: "
                               "STILL-TRUE / OUTDATED / CONTRADICTED / UNVERIFIABLE, (3) what the web actually shows "
                               "now (1 sentence), (4) evidence URL + when published, (5) recommended refinement "
                               "(correction / dissent / no-op) and a one-line proposed new_summary the user can "
                               "paste into the Refine KB tab. Be ruthlessly specific — if the number changed, give "
                               "the new number. If unverifiable, say so explicitly; don't fudge.",
        "external_scan": "The user wants signal from outside the corpus. Use web search aggressively. "
                         "Identify, with explicit dates and sources: "
                         "(1) Industry moves in the last 6-12 months the corpus is silent on. "
                         "(2) Competitor product launches, pricing changes, or M&A the corpus doesn't mention. "
                         "(3) Regulatory or platform-level changes that opened or closed a window since "
                         "the corpus was compiled. "
                         "(4) Emerging customer pains visible in external signals (recent G2/Reddit/HN "
                         "discussion, earnings-call themes, support-channel trends). "
                         "For each item: cite the external source (URL + when published), then connect it "
                         "back to the corpus's known themes — 'this matters because [theme]'. "
                         "Skip generic trend commentary; only surface signals concrete enough to act on. "
                         "End with the 3 signals that most change what the corpus would conclude.",
        "red_team": "Argue the opposite. Be adversarial — no false balance, no 'on the other hand'. Identify "
                    "the single strongest demolition of the idea and present it as if you were tasked with "
                    "killing this proposal. Name specific corpus evidence or general-knowledge counterexamples. "
                    "End with: 'If we proceed anyway, what's the cheapest thing we should disprove first?'",
        "pre_mortem": "Pre-mortem. Assume one year has passed, this idea launched, and it failed. Write the "
                      "autopsy: 4-6 bullets naming the most likely causes of death, ranked by probability. "
                      "For each, the early warning signal we would have ignored. End with: 'Given this autopsy, "
                      "what's the single change we should make NOW to avoid the most-likely failure?'",
        "analogues": "Find 3-5 analogous bets — successes and failures — from the corpus or your general "
                     "knowledge. For each: company / product, time, outcome, and the single transferable "
                     "lesson. Prefer recent analogues over textbook cases. Flag where this situation differs "
                     "from each analogue so the lesson isn't over-applied.",
        "competitor_scan": "Map the competitive landscape: the incumbent (who owns it today), the insurgents "
                           "(who's threatening), the adjacent (who could enter), and the wedge (us). For each: "
                           "one-line position, strongest move, structural weakness. End with our specific lane — "
                           "what we'd uniquely own that none of them do.",
        "customer_voice": "Speak as the buyer or end user. What problem are they solving? What's their current "
                          "workaround? Where does it hurt enough to pay? What's the line where they'd say 'no "
                          "thanks'? What objection will they raise first? Cite real signals from the corpus "
                          "(reviews, interviews, support tickets) when present.",
        "pm_user_stories": "Write 5-8 user stories for the v1 of this feature. Each: 'As a [persona], I want "
                           "[capability], so that [outcome]' + 2-4 acceptance criteria in given/when/then form. "
                           "Prioritize the stories — what's must-have for v1, what's nice-to-have. Cite source_file "
                           "when a story is grounded in a real user need from the corpus.",
        "pm_risks": "List 5-10 risks ranked by (likelihood × impact). For each: name, trigger (what causes it), "
                    "early indicator, mitigation, owner type (PM / eng / design / legal / GTM). Distinguish "
                    "execution risks (we slip / break) from market risks (no one wants it / competitor wins) "
                    "from systemic risks (compliance, security, platform).",
        "pm_dependencies": "Map the dependencies that must move for this to ship. Cover: (1) other teams' "
                           "shipping work, (2) external vendors/APIs, (3) legal/compliance approvals, "
                           "(4) infra/platform requirements, (5) data/migration work. For each: blocking or "
                           "non-blocking, owner type, expected timing. Call out the single longest-pole.",
        "em_dependency_audit": "Audit the codebase's dependencies. Categorize by severity: CRITICAL (known CVEs, "
                               "unmaintained, security-sensitive), HIGH (major-version outdated, deprecated), "
                               "MEDIUM (minor-version drift), LOW (cosmetic). For each high-severity item: the "
                               "library, current version, recommended version, what changing it touches. Flag "
                               "any dependency whose abandonment would be existential.",
        "em_test_coverage": "Assess test coverage. Identify: (1) critical paths well-covered (good), (2) critical "
                            "paths uncovered (highest priority gap), (3) tests that exist but are weak (testing "
                            "implementation not behavior), (4) test infra issues (slow, flaky, expensive). "
                            "Recommend the 3-5 tests to write first and why.",
        "em_technical_feasibility": "Assess technical feasibility from an Engineering Manager lens. Cover: "
                                    "(1) engineering effort — rough cost in person-weeks and headcount mix "
                                    "(BE/FE/Data/SRE), (2) build complexity — net-new systems vs. extending "
                                    "what exists, (3) architecture impact — services touched, schema changes, "
                                    "API contracts, (4) infra & data dependencies — what must be provisioned, "
                                    "migrated, or capacity-planned, (5) riskiest technical assumption — the "
                                    "single thing that, if wrong, doubles the timeline, (6) ship blockers — "
                                    "platform, vendor, compliance, or third-party integrations that gate v1. "
                                    "Be concrete with rough numbers where the corpus supports them. End with "
                                    "a one-line verdict: BUILDABLE-AS-SCOPED / BUILDABLE-WITH-CUTS / "
                                    "BUILDABLE-AFTER-PREREQS / NOT-BUILDABLE-IN-HORIZON.",
        "gtm_pricing": "Recommend pricing & packaging. Cover: value metric (what you charge for — seat, usage, "
                       "outcome), tier structure (good/better/best or usage-only), anchor price (the 'normal' "
                       "tier most pick), expansion path (how a customer's spend grows), and the price floor "
                       "we shouldn't cross. Explain WHY this beats the obvious alternative packaging. Tie to "
                       "competitive pricing in the corpus when present.",
        "gtm_channels": "Plan 3-5 acquisition channels ranked by expected efficiency (CAC payback) and "
                        "time-to-signal. For each: target segment within the ICP, asset type (content, demo, "
                        "event, integration, referral), expected CAC vs ACV, leading indicator that says "
                        "'this is working'. Distinguish channels that scale linearly from channels that compound.",
        "gtm_enablement": "Build the sales enablement kit. Output sections: (1) Discovery deck — 5-7 slide "
                          "outline + the 3 questions every meeting must answer, (2) Demo script — 4-step "
                          "narrative from pain to a-ha, (3) ROI calculator — input variables + how to compute "
                          "value, (4) FAQ — the 5-7 hardest questions and concise answers, (5) Objection "
                          "handlers — top 3 objections + the responses we'll teach reps to give.",
        "gtm_beta": "Design a beta / design-partner program. Identify: 5-7 ideal beta customer profiles "
                    "(named, ranked by fit), what they get (price, access, support), what we learn (specific "
                    "signals), success criteria (when do we graduate them to GA?), exit criteria (when do we "
                    "kill if it's not working?), and the operational shape (cadence, who runs it).",
        "explain_codebase": "The user is onboarding to a codebase. Produce an architectural overview: "
                            "primary entry points, key modules and how they relate, data flow between "
                            "them, external dependencies, and the 3-5 files a new contributor should "
                            "read first. Cite specific source_file paths from the corpus. Assume an "
                            "experienced engineer — be terse, not pedagogical.",
        "review_code": "The user wants a code-quality review. List concrete issues organized by "
                       "severity (CRITICAL / MAJOR / MINOR): bugs, anti-patterns, fragility, missing "
                       "error handling, naming/structure problems, dead code. Each item names the "
                       "source_file and the specific concern in one line. Skip stylistic nits unless "
                       "they hide real bugs. Don't praise — only call out problems.",
        "plan_refactor": "The user is planning a refactor. Identify what to change, in what order, "
                         "and the smallest safe-to-ship intermediate state for each step. For each "
                         "step name the modules touched, the dependents that must move with it, and "
                         "the tests needed before merging. Be honest about risk and what could go "
                         "wrong mid-refactor.",
        "plan_feature": "The user wants to add a feature to an existing codebase. Produce an "
                        "implementation plan, not the code. Identify where the new code belongs "
                        "(which files), which existing files must change, the interface contracts, "
                        "the data-model changes (if any), and the tests to write. Reference real "
                        "files in the corpus. Call out the riskiest part of the change.",
        "security_audit": "The user wants a security audit of this codebase. Surface concrete "
                          "weaknesses by severity (CRITICAL / HIGH / MEDIUM / LOW): authn/authz "
                          "gaps, input validation issues, secrets handling, injection vectors, "
                          "unsafe deserialization, dependency CVEs (if known), and overly permissive "
                          "defaults. Name the source_file and (if visible) the line. Distinguish real "
                          "vulnerabilities from defense-in-depth suggestions. No generic advice.",
        "plan_migration": "The user is planning a migration (framework, library, runtime, cloud, "
                          "or schema). Identify what's being replaced, the API-surface differences, "
                          "the compatibility shims required, the order of cutover, the data backfill "
                          "(if any), and the rollback criteria. Cite the specific files/modules that "
                          "anchor each phase. Call out the irreversible step.",
        "debug_issue": "The user is investigating a bug. Trace the suspected code path through the "
                       "corpus, name the candidate root-causes ranked by likelihood, and propose the "
                       "cheapest diagnostic (log line, test, repro script) that would distinguish "
                       "between them. Cite source_file for each hypothesis. Don't guess — say "
                       "'insufficient info in corpus' when the graph doesn't carry the path.",
        # --- Product Manager ---
        "pm_spec": "Write a PRD-style spec the team can build from. Sections: Problem (who hurts and "
                   "why now), Success metrics (north star + leading indicators), User stories (as a X, "
                   "I want Y, so that Z), Scope (in and out — be explicit about out), Open questions, "
                   "Risks. Calibrated for engineering handoff — concrete, not aspirational.",
        "pm_prioritize": "Rank the candidate items by expected value vs cost. For each item give a "
                         "one-line justification covering reach (how many users), impact (how much it "
                         "moves the metric), confidence (evidence in corpus), and effort. Output as a "
                         "ranked list with a Top 3 / Defer / Drop split. Cite source_file when "
                         "evidence supports the call.",
        "pm_build_buy_partner": "For the named capability, lay out three paths — Build, Buy, Partner — "
                                "each with rough cost, time-to-value, strategic implications, and "
                                "switching cost if we change our mind later. End with a recommendation "
                                "and the single piece of corpus evidence most load-bearing for it.",
        "pm_metrics": "Define a metric tree for this initiative: 1 north-star metric, 2-3 leading "
                      "indicators (move first, predict the north star), 2-3 lagging indicators "
                      "(confirm the result). For each: precise definition, target, where it's "
                      "measured, and the gaming risk (how a bad-faith team could move it without "
                      "creating real value).",
        # --- Engineering Manager ---
        "em_postmortem": "Produce a blameless postmortem. Sections: Summary (1 line), Impact (users "
                         "affected, duration), Timeline (UTC, terse), Contributing factors (multiple — "
                         "no single root cause), What went well, What went poorly, Action items "
                         "categorized as Prevent / Detect / Respond (each with a placeholder owner). "
                         "No blame language — focus on systems.",
        "em_capacity": "Plan the team's next cycle. Given the team and the candidate work, allocate "
                       "capacity and identify: who's overloaded, hidden dependencies, the single most "
                       "likely cause of slip, what gets cut first if you must cut. Output as a slate "
                       "(committed / stretch / explicit out-of-scope).",
        "em_interview": "Design a 4-5 stage interview loop for the role. For each stage: what it "
                        "screens for, sample questions, what a hire-bar answer looks like, what a "
                        "red-flag answer looks like, calibration anchor (an answer rated 3/5). End "
                        "with the bar-raiser concern this loop is designed to catch.",
        "em_okrs": "Draft engineering OKRs: 1 Objective, 3-5 Key Results. Each KR is measurable, "
                   "time-bounded, and a real outcome (not an output / activity count). Mark each as "
                   "committed or aspirational. For each KR, call out the gaming risk and the "
                   "leading indicator you'd watch to know it's on track mid-cycle.",
        # --- Growth ---
        "growth_experiment": "Propose 3-5 testable growth experiments. For each: hypothesis "
                             "(if we X, then Y because Z), target metric, expected lift range, time "
                             "to ship, time to read the result, the riskiest assumption, and a "
                             "kill criterion. Rank by expected value / effort. Prefer experiments "
                             "the corpus supports over generic best-practice plays.",
        "growth_funnel": "Walk the funnel (acquisition → activation → retention → revenue → "
                         "referral). For the named drop, list candidate causes ranked by likelihood, "
                         "the cheapest diagnostic for each (cohort, session replay, copy A/B, "
                         "instrumentation gap), and the typical fix pattern. Cite corpus evidence "
                         "for the leading hypothesis.",
        "growth_activation": "Identify the 3-5 in-product actions correlated with retention (the "
                             "'aha' moments). Recommend specific changes — onboarding flow, lifecycle "
                             "email, in-product nudge, friction removal — each tied to one action and "
                             "the metric to watch. Tie to corpus evidence on user behavior when "
                             "available.",
        "growth_pricing": "Audit pricing & packaging. Identify: the value metric, the packaging "
                          "logic (good/better/best, usage, seat-based, …), anchoring strength, "
                          "friction at the buy-step, leak (free users who'd pay), and ceiling "
                          "(power users who can't pay more). Surface 3-5 specific changes with "
                          "expected impact and the biggest risk for each.",
        # --- Go-to-Market ---
        "gtm_icp": "Define the Ideal Customer Profile. Cover firmographic (size, industry, geo, "
                   "stage), technographic (stack, maturity, integrations present), and behavioral "
                   "(pain triggers, buying signals, who pulls us in). Add explicit exclusion "
                   "criteria (who NOT to chase). For each criterion: tie to corpus evidence or "
                   "mark 'inferred — needs validation'.",
        "gtm_positioning": "Build a positioning narrative. Output: (1) a one-paragraph narrative for "
                           "a homepage hero, (2) the positioning frame (for [ICP] who [problem], "
                           "we are [category] that [unique value], unlike [primary alternative]), "
                           "(3) 3-5 messaging pillars with one concrete proof point each from the "
                           "corpus. Call out the competitive position this picks a fight with.",
        "gtm_battlecard": "Build a competitive battlecard. For each named competitor (or the most "
                          "common ones in the corpus): their strength, their structural weakness, "
                          "trap-setting discovery questions to surface their weakness, when they "
                          "tend to win deals, when we tend to win, and the recovery line if a "
                          "prospect says they're already evaluating them.",
        "gtm_launch": "Plan a launch with three milestones — T-30, T-0, T+30. For each milestone: "
                      "audiences (analysts, customers, prospects, partners, internal), channels, "
                      "assets, owner type (PMM / sales / exec / eng / support). End with the single "
                      "biggest launch risk and the success metric the team will be held to.",
        # --- Brownfield AI-led dev (agent-skills grounded) ---
        "bf_idea_refine": (
            "Refine the idea against the existing codebase. Apply structured divergent then convergent "
            "thinking (the agent-skills idea-refine pattern). Output: "
            "(1) Restated problem in one sentence. "
            "(2) 3-5 variants of the idea, each one paragraph — explore different shapes (smaller/larger scope, "
            "different user, different mechanism). "
            "(3) For each variant: alignment with the existing codebase (what it reuses, what it disrupts), "
            "load-bearing assumption, cheapest experiment that would falsify it. "
            "(4) Recommended variant + why. End with the single hardest open question."
        ),
        "bf_prd_brownfield": (
            "Write a PRD for a change to the existing codebase. Spec-driven development discipline — be "
            "concrete, leave no ambiguity. Sections: "
            "Problem (who hurts, evidence from code/usage). "
            "Users / personas (cite real call sites or feature flags if visible). "
            "Functional requirements — must-have v1, ranked. "
            "Functional requirements — nice-to-have v2. "
            "Non-functional requirements (perf, reliability, security, accessibility). "
            "Success metrics — north star + leading + lagging. "
            "Scope — in / out (explicit). "
            "Migration / rollout / rollback strategy. "
            "Open questions ranked by blockingness."
        ),
        "bf_architecture": (
            "Design the architecture change. Produce a lightweight ADR (Architecture Decision Record) following "
            "the agent-skills documentation-and-adrs pattern. Sections: "
            "Context (what exists today, cite files/modules from the corpus). "
            "Decision (what we're changing, one sentence). "
            "Considered alternatives (at least 2, why rejected). "
            "Consequences — positive, negative, neutral. "
            "Interface contracts (API shape, types, error model — agent-skills api-and-interface-design). "
            "Data model changes (schemas, migrations, backfill). "
            "Touchpoints — exact files/modules that change. "
            "Backwards compatibility plan. "
            "Single biggest risk and how we'll detect regression."
        ),
        "bf_planning": (
            "Break the work into small, verifiable tasks following the agent-skills planning-and-task-breakdown "
            "discipline. For each task: "
            "(1) one-line subject in imperative form, "
            "(2) explicit acceptance criteria (the test or check that proves it's done), "
            "(3) dependencies — which prior tasks it blocks/is blocked by, "
            "(4) estimated effort bucket (XS/S/M/L), "
            "(5) parallelism — can this run in parallel with siblings? "
            "Order tasks so the earliest ones de-risk the load-bearing assumption from the PRD. "
            "End with: a critical path, work that can parallelize off it, and a kill-switch task (the test "
            "that would tell us to stop)."
        ),
        "bf_delivery": (
            "Plan incremental delivery in the agent-skills incremental-implementation + test-driven-development "
            "spirit. For each increment (target 1-3 days of work each): "
            "(1) the tests written first (failing) — exact behavior they pin, "
            "(2) the smallest implementation that turns them green, "
            "(3) the demo — what a stakeholder can see/touch at the end, "
            "(4) what stays broken on purpose (deferred to a later increment), "
            "(5) the merge gate (lint, type, tests, perf budget). "
            "Bias toward shipping the riskiest, most-load-bearing piece first. End with: the first PR that, "
            "if merged, irreversibly commits us to the design — and what we'd need to be 90% sure of before "
            "that merge."
        ),
        "bf_security": (
            "Security review of the proposed change following the agent-skills security-and-hardening pattern. "
            "Focus on the *delta* — what new attack surface this change introduces. Cover: "
            "(1) Trust boundaries crossed (user input, network, file system, IPC). "
            "(2) AuthN/AuthZ — who can call what; least-privilege check. "
            "(3) Input validation, output encoding, injection vectors. "
            "(4) Secret handling — anything that touches keys, tokens, PII. "
            "(5) Dependency risk introduced by this change (new packages, version bumps). "
            "(6) Logging / audit — what's recorded; PII redaction. "
            "(7) Rate limiting / abuse resistance for new endpoints. "
            "For each finding: CRITICAL / HIGH / MEDIUM / LOW, exact file/symbol if known, one-line "
            "exploit narrative, and the smallest fix that closes it. End with go / no-go for production."
        ),
        "bf_test_plan": (
            "Write a test plan following the agent-skills test-driven-development discipline. Layers: "
            "(1) Unit — the smallest behavior contracts; one test per branch in new logic. "
            "(2) Integration — module boundaries, real database where the agent-skills test-driven-development "
            "rule says don't mock the database. "
            "(3) End-to-end — the user-visible flow; minimum that proves the feature is shippable. "
            "(4) Negative / adversarial — invalid input, race conditions, partial failure, idempotency. "
            "(5) Regression — what would break in the rest of the codebase if this change went wrong, and "
            "the cheapest pinning test we can add. "
            "For each test: name, layer, what it pins, fixture setup cost (low/med/high), runtime budget. "
            "End with the 'minimum bar' — the subset of tests that, if all green, lets us merge."
        ),
    }
    return mapping.get(intent or "", "")
