"""Playbooks — multi-step workflows that produce a typed Artifact.

A Playbook is a fixed chain of steps. Each step is one of:

  - `intent_turn`: a one-shot LLM call shaped by an existing Intent + the
    workspace's rubric + (optionally) prior step outputs as context.
  - `foresight`: a multi-persona, multi-round debate using the existing
    foresight runner. Creates a real foresight session so it also surfaces
    in the ForeSight tab.
  - `synthesize`: the final pass that produces the typed Artifact (tldr +
    structured sections + raw markdown).

Runs execute in a background thread; their state (per-step status, tokens,
output) is persisted as a JSON file under
data/workspaces/<id>/playbook_runs/ and re-read by the polling endpoint.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from typing import Any

import artifacts
import conversations as conv_store
import foresight
import memory as memory_store
import rubrics as rubric_store
import simulate as sim_store
from graphify_runner import _anthropic_client, render_graph_context, rich_query
from workspaces import Workspace


# ---------- Playbook definitions ------------------------------------------

# Each step's `intent` references a real Intent id from rubrics.INTENT_LABELS.
# `output_field` is the artifact section name this step populates by default
# (the final synth re-organizes everything into the structured artifact).
#
# `personas` (foresight steps) reference persona ids from foresight.list_personas.

PLAYBOOKS: dict[str, dict[str, Any]] = {
    "find_unexplored_ideas": {
        "id": "find_unexplored_ideas",
        "label": "Find unexplored ideas",
        "tagline": "Diverge wide (no constraints) → inventory existing themes → white space + bridges → external signals → competitive landscape → debate → fresh wedges outside the Appfire moat with GTM motion → against-all-odds rescue.",
        "expected_duration_s": 660,
        "accepts_source_types": [],
        "artifact_type": "OpportunityScan",
        "steps": [
            {"id": "diverge",     "label": "Diverge — 20 unconstrained candidate ideas", "type": "divergent",
             "count": 20, "temperature": 1.0},
            {"id": "explore",     "label": "Open-ended exploration",           "type": "intent_turn", "intent": "explore"},
            {"id": "inventory",   "label": "Inventory existing themes",        "type": "intent_turn", "intent": "synthesize"},
            {"id": "gaps",        "label": "Surface white space + bridges",    "type": "intent_turn", "intent": "find_gaps"},
            {"id": "external",    "label": "Scan external signals (web)",      "type": "intent_turn", "intent": "external_scan"},
            {"id": "competitors", "label": "Scan competitive landscape",       "type": "intent_turn", "intent": "competitor_scan"},
            {"id": "debate",        "label": "Bull / Bear / Customer / Competitor debate", "type": "foresight",
             "personas": ["preset:bull", "preset:bear", "preset:customer", "preset:competitor"], "rounds": 1},
            {"id": "outside_moat",  "label": "Outside the moat — new wedges Appfire hasn't tried, with GTM motion", "type": "intent_turn", "intent": "outside_the_moat"},
            {"id": "path_to_win",   "label": "Against-all-odds — wedges for the Tier 2 / Tier 3 candidates", "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",         "label": "Compose opportunity brief",        "type": "synthesize",
             "sections": [
                 "TL;DR — top 3 unexplored ideas",
                 "Inventory: themes already covered (and density)",
                 "Internal white space",
                 "Bridges between disconnected themes",
                 "External signals that changed what we'd conclude",
                 "Competitive landscape — incumbents, insurgents, adjacent",
                 "Competitive lane we'd uniquely own",
                 "Top opportunities (ranked) — Tier 1 (strong + executable + on-rubric)",
                 "Rubric tensions — Tier 2 (strong + executable but violate rules) + proposed rubric update",
                 "Candidates for human review — Tier 3 (interesting but didn't clear the bar; what'd have to be true to graduate)",
                 "Per opportunity: cheapest validation",
                 "Per opportunity: load-bearing assumption",
                 "Adjacency risk — where we'd be punching above our weight",
                 "OUTSIDE-THE-MOAT — fresh wedges Appfire has NOT yet tried, each with a credible GTM motion outside the Atlassian Marketplace. 3-5 wedges. For each: THE WEDGE (named buyer + trigger) · WHY THIS IS NEW FOR APPFIRE · GO-TO-MARKET MOTION (direct enterprise sales / PLG / vertical resellers / channel partners / content+community / certification-as-channel / named partnership / small anchor M&A — name the first 3 acquisition mechanisms with specifics, not categories) · UNFAIR ADVANTAGE WE'D BORROW from Appfire's existing footprint · CHEAPEST PROOF (90 days, < $50K, with the specific metric that has to move) · HONEST ODDS (tail bet / long shot / real chance). End with THE WEDGE I'D STAKE THE MOST ON + which Appfire leader (CRO, CPO, VP Corp Dev, VP New Markets) should own it.",
                 "AGAINST-ALL-ODDS — narrow paths to win for the Tier 2 / Tier 3 ideas the debate pushed back on: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                 "What we'd need to know before committing",
             ]},
        ],
    },
    "discover_opportunity": {
        "id": "discover_opportunity",
        "label": "Discover product opportunities",
        "tagline": "Diverge wide → ground in analogues → scan the competitive landscape → debate the top one (Bull/Bear/Customer, 2 rounds) → fresh wedges outside the Appfire moat with GTM motion → against-all-odds rescue.",
        "expected_duration_s": 540,
        "accepts_source_types": [],
        "artifact_type": "OpportunityScan",
        "steps": [
            {"id": "diverge",    "label": "Diverge — 15 unconstrained candidate ideas", "type": "divergent",
             "count": 15, "temperature": 1.0},
            {"id": "discover",   "label": "Surface candidate ideas",         "type": "intent_turn", "intent": "product_idea"},
            {"id": "analogues",  "label": "Ground in historical analogues",  "type": "intent_turn", "intent": "analogues"},
            {"id": "competitors","label": "Scan competitive landscape",      "type": "intent_turn", "intent": "competitor_scan"},
            {"id": "evaluate",   "label": "Pressure-test the top idea",      "type": "intent_turn", "intent": "evaluate"},
            {"id": "debate",     "label": "Debate: Bull / Bear / Customer (2 rounds)", "type": "foresight",
             "personas": ["preset:bull", "preset:bear", "preset:customer"], "rounds": 2},
            {"id": "feasibility", "label": "Assess technical feasibility",     "type": "intent_turn", "intent": "em_technical_feasibility"},
            {"id": "outside_moat","label": "Outside the moat — new wedges Appfire hasn't tried, with GTM motion", "type": "intent_turn", "intent": "outside_the_moat"},
            {"id": "path_to_win", "label": "Against-all-odds — wedges despite Bear / feasibility pushback", "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",       "label": "Compose opportunity brief",       "type": "synthesize",
             "sections": ["Top opportunity — Tier 1 (strong + executable + on-rubric)",
                          "Closest analogue + lesson",
                          "Competitive landscape — incumbents, insurgents, adjacent",
                          "Competitive lane we'd uniquely own",
                          "Rubric tensions — Tier 2 (strong + executable but violate rules) + proposed rubric update",
                          "Candidates for human review — Tier 3 (interesting but didn't clear the bar; what'd have to be true to graduate)",
                          "ARR estimate", "Target buyer", "Biggest risk",
                          "Technical feasibility — verdict + biggest blocker",
                          "Position updates across the debate",
                          "OUTSIDE-THE-MOAT — fresh wedges Appfire has NOT yet tried, each with a credible GTM motion outside the Atlassian Marketplace. 3-5 wedges. For each: THE WEDGE (named buyer + trigger) · WHY THIS IS NEW FOR APPFIRE · GO-TO-MARKET MOTION (direct enterprise sales / PLG / vertical resellers / channel partners / content+community / certification-as-channel / named partnership / small anchor M&A — name the first 3 acquisition mechanisms with specifics, not categories) · UNFAIR ADVANTAGE WE'D BORROW from Appfire's existing footprint · CHEAPEST PROOF (90 days, < $50K, with the specific metric that has to move) · HONEST ODDS (tail bet / long shot / real chance). End with THE WEDGE I'D STAKE THE MOST ON + which Appfire leader (CRO, CPO, VP Corp Dev, VP New Markets) should own it.",
                          "AGAINST-ALL-ODDS — narrow paths to win despite the Bear arguments + feasibility blockers: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                          "Convergent signal", "Recommended next step"]},
        ],
    },
    "pressure_test_strategy": {
        "id": "pressure_test_strategy",
        "label": "Pressure-test a strategy",
        "tagline": "Explore tensions, evaluate balance, red-team adversarially, 4-persona debate, then fresh wedges outside the Appfire moat with GTM motion + against-all-odds rescue if the strategy gets shredded.",
        "expected_duration_s": 420,
        "accepts_source_types": ["OpportunityScan", "StrategyBrief"],
        "artifact_type": "StrategyBrief",
        "steps": [
            {"id": "explore",     "label": "Surface tensions in the strategy", "type": "intent_turn", "intent": "explore"},
            {"id": "evaluate",    "label": "Evaluate load-bearing assumptions", "type": "intent_turn", "intent": "evaluate"},
            {"id": "red_team",    "label": "Red-team — argue the opposite",   "type": "intent_turn", "intent": "red_team"},
            {"id": "foresight",   "label": "4-persona debate (2 rounds)",     "type": "foresight",
             "personas": ["preset:bull", "preset:bear", "preset:customer", "preset:competitor"], "rounds": 2},
            {"id": "outside_moat","label": "Outside the moat — new wedges Appfire hasn't tried, with GTM motion", "type": "intent_turn", "intent": "outside_the_moat"},
            {"id": "path_to_win", "label": "Against-all-odds — narrow paths to win despite red-team + bear",  "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",       "label": "Compose strategy brief",          "type": "synthesize",
             "sections": ["Recommendation", "Strongest argument FOR",
                          "Strongest argument AGAINST", "Red-team's killing blow",
                          "Convergent claims", "Divergent claims",
                          "OUTSIDE-THE-MOAT — fresh wedges Appfire has NOT yet tried, each with a credible GTM motion outside the Atlassian Marketplace. 3-5 wedges. For each: THE WEDGE (named buyer + trigger) · WHY THIS IS NEW FOR APPFIRE · GO-TO-MARKET MOTION (direct enterprise sales / PLG / vertical resellers / channel partners / content+community / certification-as-channel / named partnership / small anchor M&A — name the first 3 acquisition mechanisms with specifics, not categories) · UNFAIR ADVANTAGE WE'D BORROW from Appfire's existing footprint · CHEAPEST PROOF (90 days, < $50K, with the specific metric that has to move) · HONEST ODDS (tail bet / long shot / real chance). End with THE WEDGE I'D STAKE THE MOST ON + which Appfire leader (CRO, CPO, VP Corp Dev, VP New Markets) should own it.",
                          "AGAINST-ALL-ODDS — narrow paths to win despite the red-team and bear arguments: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                          "Watch indicators"]},
        ],
    },
    "product_strategy_director": {
        "id": "product_strategy_director",
        "label": "Product Strategy Director",
        "tagline": "Full-scope strategic review: current product + repo · MOAT audit (Product/Company/Distribution) · competitive · market shifts · opportunities · analogues · build/buy/partner · pre-mortem · red-team · against-all-odds rescue · outside-the-moat wedges with GTM motion · 5-persona debate → Kill/Continue/Build verdict + top 3 recommendations across Product, Engineering, GTM, Marketing.",
        "expected_duration_s": 840,
        "accepts_source_types": ["OpportunityScan", "StrategyBrief", "CodebaseAudit"],
        "artifact_type": "StrategyBrief",
        "steps": [
            {"id": "state_of_product", "label": "Audit current state — product, repo, traction",         "type": "intent_turn", "intent": "explain_codebase"},
            {"id": "customer_voice",   "label": "Who we serve today (and who we lost)",                  "type": "intent_turn", "intent": "customer_voice"},
            {"id": "moat_present",     "label": "MOAT — present (graph-anchored, 5 layers, verdict per layer)", "type": "intent_turn", "intent": "moat_audit_present"},
            {"id": "competitors",      "label": "Competitive landscape — incumbents · insurgents · adjacent", "type": "intent_turn", "intent": "competitor_scan"},
            {"id": "market_shifts",    "label": "Market shifts since the corpus was written",            "type": "intent_turn", "intent": "external_scan"},
            {"id": "moat_future",      "label": "MOAT — future paths (walk-or-skip verdict per path)",  "type": "intent_turn", "intent": "moat_audit_future"},
            {"id": "analogues",        "label": "Historical analogues — how similar bets resolved",      "type": "intent_turn", "intent": "analogues"},
            {"id": "build_buy",        "label": "Build / Buy / Partner for the top WALK paths",         "type": "intent_turn", "intent": "pm_build_buy_partner"},
            {"id": "pre_mortem",       "label": "Pre-mortem — imagine the strategy failed in 12 months","type": "intent_turn", "intent": "pre_mortem"},
            {"id": "red_team",         "label": "Red-team — argue the opposite verdict",                "type": "intent_turn", "intent": "red_team"},
            {"id": "path_to_win",      "label": "Against-all-odds — narrow paths to win despite the SKIPs / KILLs", "type": "intent_turn", "intent": "path_to_win"},
            {"id": "outside_moat",     "label": "Outside the moat — new wedges Appfire hasn't tried, with GTM motion", "type": "intent_turn", "intent": "outside_the_moat"},
            {"id": "debate",           "label": "Investor / Bull / Bear / Customer / Competitor debate (2 rounds)", "type": "foresight",
             "personas": ["preset:investor", "preset:bull", "preset:bear", "preset:customer", "preset:competitor"], "rounds": 2},
            {"id": "synth", "label": "Compose Product Strategy Director brief", "type": "synthesize",
             "sections": [
                 "TL;DR — Kill / Continue / Build verdict + the single TOP future MOAT path to walk + headline rationale",
                 "Current state — product · repo · traction (where we are today)",
                 "Customers we serve today (and segments we've lost)",
                 "MOAT — PRESENT (graph-anchored): one row per layer (Product · Company · Distribution · Data · Network) with STRENGTH 1-5 · DECAY rate · VERDICT (REAL / THIN / ILLUSION) · single weakness · source_file evidence. End with STRONGEST and WEAKEST today.",
                 "Competitive landscape — incumbents · insurgents · adjacent (with Sherlocking risk per row)",
                 "Market shifts since the corpus was written (and what they invalidate)",
                 "MOAT — FUTURE paths: one row per candidate path with WHAT TO BUILD · moat type unlocked · time-to-defensibility · load-bearing assumption · cheapest validation · WALK or SKIP verdict + one-line rationale. Rank WALK paths; show SKIP paths too (don't silently drop them).",
                 "Historical analogue + lesson for the leading bet",
                 "Risks — pre-mortem failure mode · earliest warning signal · red-team's killing blow · our counter",
                 "Persona-debate convergence + the strongest divergence (which voice was most under-weighted)",
                 "VERDICT — Kill / Continue / Build at the product level: for each, one line on WHAT and one on WHY",
                 "VERDICT — direction on each FUTURE MOAT path: WALK / SKIP / WAIT-FOR-SIGNAL with one-line rationale and the kill-trigger that would flip the verdict",
                 "AGAINST-ALL-ODDS — narrow paths to win despite the SKIPs and KILLs above: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                 "OUTSIDE-THE-MOAT — fresh wedges Appfire has NOT yet tried, each with a credible GTM motion outside the Atlassian Marketplace. 3-5 wedges. For each: THE WEDGE (named buyer + trigger) · WHY THIS IS NEW FOR APPFIRE · GO-TO-MARKET MOTION (direct enterprise sales / PLG / vertical resellers / channel partners / content+community / certification-as-channel / named partnership / small anchor M&A — name the first 3 acquisition mechanisms with specifics, not categories) · UNFAIR ADVANTAGE WE'D BORROW from Appfire's existing footprint · CHEAPEST PROOF (90 days, < $50K, with the specific metric that has to move) · HONEST ODDS (tail bet / long shot / real chance). End with THE WEDGE I'D STAKE THE MOST ON + which Appfire leader (CRO, CPO, VP Corp Dev, VP New Markets) should own it.",
                 "Top 3 Product recommendations — ranked, each tagged with which future moat path it walks + biggest risk",
                 "Top 3 Engineering recommendations — ranked, each with effort + biggest blocker",
                 "Top 3 GTM recommendations — ranked, each with channel + ICP",
                 "Top 3 Marketing recommendations — ranked, each with audience + message",
                 "First-90-day execution — capital + capacity allocation · watch indicators · first-30-day checklist · single load-bearing assumption + cheapest falsifying experiment",
             ]},
        ],
    },
    "build_buy_partner": {
        "id": "build_buy_partner",
        "label": "Decide build vs buy vs partner",
        "tagline": "Three paths, competitor landscape, pre-mortem, Investor/Customer/Competitor debate, then against-all-odds wedges if the leading path looks weak.",
        "expected_duration_s": 300,
        "accepts_source_types": ["OpportunityScan", "StrategyBrief"],
        "artifact_type": "BuildBuyDecision",
        "steps": [
            {"id": "options",    "label": "Lay out the three paths",         "type": "intent_turn", "intent": "pm_build_buy_partner"},
            {"id": "competitors","label": "Scan competitor landscape",       "type": "intent_turn", "intent": "competitor_scan"},
            {"id": "pre_mortem", "label": "Pre-mortem on the leading path",  "type": "intent_turn", "intent": "pre_mortem"},
            {"id": "foresight",  "label": "Investor / Customer / Competitor debate", "type": "foresight",
             "personas": ["preset:investor", "preset:customer", "preset:competitor"], "rounds": 1},
            {"id": "feasibility","label": "Assess feasibility of the Build path", "type": "intent_turn", "intent": "em_technical_feasibility"},
            {"id": "path_to_win","label": "Against-all-odds — wedges for the SKIP-ed paths",   "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",      "label": "Compose decision brief",          "type": "synthesize",
             "sections": ["Recommendation", "Build — cost/time/risk",
                          "Buy — cost/time/risk", "Partner — cost/time/risk",
                          "Technical feasibility of Build (EM view)",
                          "Competitive lane we'd own", "Most-likely failure mode",
                          "Switching cost if we change our mind",
                          "AGAINST-ALL-ODDS — narrow paths to win on the SKIP-ed options (or the leading path despite pre-mortem / debate pushback): 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                          "Load-bearing evidence"]},
        ],
    },
    "draft_prd": {
        "id": "draft_prd",
        "label": "Draft a PRD",
        "tagline": "Spec + user stories + metrics + risks. Engineering-ready handoff.",
        "expected_duration_s": 220,
        "accepts_source_types": ["OpportunityScan", "StrategyBrief", "BuildBuyDecision"],
        "artifact_type": "PRDDraft",
        "steps": [
            {"id": "spec",       "label": "Write the PRD problem + scope",   "type": "intent_turn", "intent": "pm_spec"},
            {"id": "stories",    "label": "Write user stories",              "type": "intent_turn", "intent": "pm_user_stories"},
            {"id": "metrics",    "label": "Define success metrics",          "type": "intent_turn", "intent": "pm_metrics"},
            {"id": "risks",      "label": "Identify risks + mitigations",    "type": "intent_turn", "intent": "pm_risks"},
            {"id": "feasibility","label": "Assess technical feasibility",    "type": "intent_turn", "intent": "em_technical_feasibility"},
            {"id": "synth",      "label": "Compose PRD draft",               "type": "synthesize",
             "sections": ["Problem", "Users / personas",
                          "User stories — must-have v1",
                          "User stories — nice-to-have",
                          "Success metrics — north star + leading + lagging",
                          "Scope — in", "Scope — out",
                          "Technical feasibility — verdict + effort + blockers",
                          "Risks ranked by likelihood × impact",
                          "Open questions", "Riskiest assumption"]},
        ],
    },
    "plan_launch": {
        "id": "plan_launch",
        "label": "Plan a launch (GTM)",
        "tagline": "ICP → positioning → pricing → channels → battlecard → launch milestones. Full launch kit.",
        "expected_duration_s": 360,
        "accepts_source_types": ["PRDDraft", "StrategyBrief", "OpportunityScan"],
        "artifact_type": "LaunchPlan",
        "steps": [
            {"id": "icp",         "label": "Define the ICP",                 "type": "intent_turn", "intent": "gtm_icp"},
            {"id": "positioning", "label": "Build positioning narrative",    "type": "intent_turn", "intent": "gtm_positioning"},
            {"id": "pricing",     "label": "Recommend pricing + packaging",  "type": "intent_turn", "intent": "gtm_pricing"},
            {"id": "channels",    "label": "Plan acquisition channels",      "type": "intent_turn", "intent": "gtm_channels"},
            {"id": "battlecard",  "label": "Build competitive battlecard",   "type": "intent_turn", "intent": "gtm_battlecard"},
            {"id": "milestones",  "label": "Plan T-30 / T-0 / T+30 launch milestones", "type": "intent_turn", "intent": "gtm_launch"},
            {"id": "synth",       "label": "Compose launch plan",            "type": "synthesize",
             "sections": ["ICP — firmographic + technographic + behavioral",
                          "Exclusion criteria",
                          "Positioning narrative (1 paragraph)",
                          "Messaging pillars",
                          "Pricing + packaging recommendation",
                          "Top 3 acquisition channels (ranked)",
                          "Battlecard summary",
                          "T-30 milestones", "T-0 milestones", "T+30 milestones",
                          "Biggest launch risk", "Success metric"]},
        ],
    },
    "codebase_health": {
        "id": "codebase_health",
        "label": "Codebase health check",
        "tagline": "Architecture + quality + deps + test coverage + security on an ingested repo.",
        "expected_duration_s": 240,
        "accepts_source_types": [],
        "artifact_type": "CodebaseAudit",
        "steps": [
            {"id": "explain",     "label": "Explain the codebase",           "type": "intent_turn", "intent": "explain_codebase"},
            {"id": "review",      "label": "Review code quality",            "type": "intent_turn", "intent": "review_code"},
            {"id": "deps",        "label": "Audit dependencies",             "type": "intent_turn", "intent": "em_dependency_audit"},
            {"id": "tests",       "label": "Assess test coverage",           "type": "intent_turn", "intent": "em_test_coverage"},
            {"id": "security",    "label": "Security audit",                 "type": "intent_turn", "intent": "security_audit"},
            {"id": "synth",       "label": "Compose codebase audit",         "type": "synthesize",
             "sections": ["Architecture overview", "Files to read first",
                          "Critical code issues", "Major code issues",
                          "Critical dependency issues", "Major dependency issues",
                          "Top test-coverage gaps",
                          "Security: CRITICAL", "Security: HIGH/MEDIUM",
                          "Recommended next moves (sequenced)"]},
        ],
    },
    "audit_kb_freshness": {
        "id": "audit_kb_freshness",
        "label": "Audit KB freshness",
        "tagline": "Inventory load-bearing claims, verify against the live web, output a list of refinements to apply.",
        "expected_duration_s": 300,
        "accepts_source_types": [],
        "artifact_type": "KBHealthReport",
        "steps": [
            {"id": "audit",     "label": "Inventory load-bearing claims",     "type": "intent_turn", "intent": "audit_claims"},
            {"id": "verify",    "label": "Verify against the live web",        "type": "intent_turn", "intent": "verify_load_bearing"},
            {"id": "synth",     "label": "Compose KB health report",           "type": "synthesize",
             "sections": [
                 "TL;DR — count of stale / contradicted claims",
                 "STILL-TRUE — claims the web confirms",
                 "OUTDATED — claims that should be corrected (with proposed new_summary)",
                 "CONTRADICTED — claims the web disagrees with",
                 "UNVERIFIABLE — no authoritative external signal found",
                 "Recommended refinements (correction / dissent) — paste-ready",
                 "Single claim most worth fixing first",
             ]},
        ],
    },
    "premortem_plan": {
        "id": "premortem_plan",
        "label": "Pre-mortem on a plan",
        "tagline": "Stress-test something you're about to commit to — failure imagined + red-team + quick debate + against-all-odds wedges to ship anyway.",
        "expected_duration_s": 240,
        "accepts_source_types": ["StrategyBrief", "PRDDraft", "LaunchPlan", "BuildBuyDecision", "OpportunityScan"],
        "artifact_type": "StrategyBrief",
        "steps": [
            {"id": "pre_mortem", "label": "Pre-mortem: imagine it failed",   "type": "intent_turn", "intent": "pre_mortem"},
            {"id": "red_team",   "label": "Red-team: argue the opposite",    "type": "intent_turn", "intent": "red_team"},
            {"id": "simulate",   "label": "Quick simulate (Bear/Customer/Competitor)", "type": "simulate",
             "personas": ["bear", "customer", "competitor"], "horizon": "1y"},
            {"id": "path_to_win","label": "Against-all-odds — wedges to ship despite the failure modes", "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",      "label": "Compose stress-test brief",       "type": "synthesize",
             "sections": ["Most-likely cause of failure",
                          "Early warning signal we'd ignore",
                          "Red-team's killing blow",
                          "Bear / Customer / Competitor convergence",
                          "AGAINST-ALL-ODDS — narrow paths that survive the failure modes above: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                          "Single change to make NOW",
                          "What to monitor in the first 30 days"]},
        ],
    },
    # --- Brownfield AI-led development chain (agent-skills grounded) -------
    # Each stage emits a typed artifact; downstream stages consume the prior
    # one via source_artifact_id, so the Artifacts tab shows the chain.
    "bf_idea_refinement": {
        "id": "bf_idea_refinement",
        "label": "1. Refine an idea (brownfield)",
        "tagline": "Idea against the existing codebase: variants, red-team, convergence, against-all-odds wedges. Outputs IdeaRefinement.",
        "expected_duration_s": 240,
        "accepts_source_types": [],
        "artifact_type": "IdeaRefinement",
        "steps": [
            {"id": "explore",     "label": "Explore the idea openly",       "type": "intent_turn", "intent": "explore"},
            {"id": "refine",      "label": "Diverge → converge (idea-refine)", "type": "intent_turn", "intent": "bf_idea_refine"},
            {"id": "red_team",    "label": "Red-team the recommended variant", "type": "intent_turn", "intent": "red_team"},
            {"id": "path_to_win", "label": "Against-all-odds — wedges if the red-team's killing blow lands", "type": "intent_turn", "intent": "path_to_win"},
            {"id": "synth",       "label": "Compose IdeaRefinement",         "type": "synthesize",
             "sections": ["Problem (restated)",
                          "Variants considered",
                          "Recommended variant + why",
                          "Codebase alignment — what reuses, what disrupts",
                          "Load-bearing assumption",
                          "Cheapest falsifying experiment",
                          "Red-team's killing blow",
                          "AGAINST-ALL-ODDS — narrow paths to ship the recommended variant despite the red-team's killing blow: 3-5 wedges, each with THE WEDGE · unfair advantage we'd need to manufacture · contrarian move · 30/60/90-day proof (< $50K) · asymmetry check · honest probability (tail bet / long shot / real chance). End with THE PATH I'D STAKE THE MOST ON.",
                          "Single hardest open question"]},
        ],
    },
    "bf_prd": {
        "id": "bf_prd",
        "label": "2. Write the PRD (brownfield)",
        "tagline": "Spec-driven PRD for a change to the existing codebase. Consumes IdeaRefinement.",
        "expected_duration_s": 240,
        "accepts_source_types": ["IdeaRefinement"],
        "artifact_type": "PRD",
        "steps": [
            {"id": "spec",        "label": "Write problem + scope (spec-driven)", "type": "intent_turn", "intent": "bf_prd_brownfield"},
            {"id": "stories",     "label": "Write user stories",            "type": "intent_turn", "intent": "pm_user_stories"},
            {"id": "metrics",     "label": "Define success metrics",        "type": "intent_turn", "intent": "pm_metrics"},
            {"id": "risks",       "label": "Identify risks + mitigations",  "type": "intent_turn", "intent": "pm_risks"},
            {"id": "feasibility", "label": "Technical feasibility check",   "type": "intent_turn", "intent": "em_technical_feasibility"},
            {"id": "synth",       "label": "Compose PRD",                   "type": "synthesize",
             "sections": ["Problem",
                          "Users / personas",
                          "Functional requirements — must-have v1",
                          "Functional requirements — nice-to-have v2",
                          "Non-functional requirements",
                          "Success metrics — north star + leading + lagging",
                          "Scope — in",
                          "Scope — out",
                          "Migration / rollout / rollback strategy",
                          "Technical feasibility — verdict + blockers",
                          "Risks ranked by likelihood × impact",
                          "Open questions ranked by blockingness"]},
        ],
    },
    "bf_architecture": {
        "id": "bf_architecture",
        "label": "3. Design the architecture (ADR)",
        "tagline": "Lightweight ADR + interface contracts + data-model deltas. Consumes PRD or IdeaRefinement.",
        "expected_duration_s": 240,
        "accepts_source_types": ["PRD", "IdeaRefinement"],
        "artifact_type": "ArchitectureDoc",
        "steps": [
            {"id": "explain",     "label": "Explain the relevant codebase",  "type": "intent_turn", "intent": "explain_codebase"},
            {"id": "design",      "label": "ADR — design the change",        "type": "intent_turn", "intent": "bf_architecture"},
            {"id": "feasibility", "label": "Stress-test feasibility",        "type": "intent_turn", "intent": "em_technical_feasibility"},
            {"id": "debate",      "label": "Delivery / Platform / Investor debate","type": "foresight",
             "personas": ["preset:delivery_first", "preset:platform_first", "preset:investor"], "rounds": 1},
            {"id": "synth",       "label": "Compose ArchitectureDoc",        "type": "synthesize",
             "sections": ["Context (what exists today)",
                          "Decision (one sentence)",
                          "Considered alternatives + why rejected",
                          "Consequences — positive / negative / neutral",
                          "Interface contracts (API shape, types, errors)",
                          "Data model changes (schemas, migrations, backfill)",
                          "Touchpoints — files / modules that change",
                          "Backwards compatibility plan",
                          "Biggest risk + regression-detection plan"]},
        ],
    },
    "bf_planning": {
        "id": "bf_planning",
        "label": "4. Plan the work (task breakdown)",
        "tagline": "Small, ordered, verifiable tasks with acceptance criteria + dependencies. Consumes ArchitectureDoc or PRD.",
        "expected_duration_s": 180,
        "accepts_source_types": ["ArchitectureDoc", "PRD"],
        "artifact_type": "DeliveryPlan",
        "steps": [
            {"id": "tasks",       "label": "Break work into tasks",          "type": "intent_turn", "intent": "bf_planning"},
            {"id": "deps",        "label": "Map dependencies + parallelism", "type": "intent_turn", "intent": "pm_dependencies"},
            {"id": "synth",       "label": "Compose DeliveryPlan",           "type": "synthesize",
             "sections": ["Task list (id · subject · acceptance · effort)",
                          "Dependency graph",
                          "Critical path",
                          "Parallelizable work",
                          "Kill-switch task (the test that says stop)",
                          "Earliest task that de-risks the load-bearing assumption",
                          "Estimated total effort"]},
        ],
    },
    "bf_delivery": {
        "id": "bf_delivery",
        "label": "5. Plan delivery (TDD + incremental)",
        "tagline": "Incremental, TDD-first delivery plan. Consumes DeliveryPlan.",
        "expected_duration_s": 200,
        "accepts_source_types": ["DeliveryPlan"],
        "artifact_type": "DeliveryReport",
        "steps": [
            {"id": "delivery",    "label": "Plan increments (TDD-first)",    "type": "intent_turn", "intent": "bf_delivery"},
            {"id": "review",      "label": "Pre-review the riskiest increment", "type": "intent_turn", "intent": "review_code"},
            {"id": "synth",       "label": "Compose DeliveryReport",         "type": "synthesize",
             "sections": ["Increments (ordered) — tests written first, implementation, demo, merge gate",
                          "Sequencing rationale — why this order de-risks the most",
                          "What stays broken on purpose (deferred)",
                          "First PR that irreversibly commits us — and what we need to be 90% sure of",
                          "Pre-review notes on the riskiest increment",
                          "Rollback playbook per increment"]},
        ],
    },
    "bf_security_review": {
        "id": "bf_security_review",
        "label": "6. Security review",
        "tagline": "Threat-model the delta — new attack surface, AuthZ, secrets, deps. Consumes DeliveryPlan / ArchitectureDoc.",
        "expected_duration_s": 220,
        "accepts_source_types": ["DeliveryPlan", "ArchitectureDoc", "DeliveryReport"],
        "artifact_type": "SecurityReview",
        "steps": [
            {"id": "audit",       "label": "Security audit (codebase view)", "type": "intent_turn", "intent": "security_audit"},
            {"id": "deps",        "label": "Dependency risk",                "type": "intent_turn", "intent": "em_dependency_audit"},
            {"id": "review",      "label": "Threat-model the change",        "type": "intent_turn", "intent": "bf_security"},
            {"id": "synth",       "label": "Compose SecurityReview",         "type": "synthesize",
             "sections": ["Trust boundaries crossed by this change",
                          "AuthN / AuthZ — who can call what",
                          "Input validation + injection vectors",
                          "Secret handling",
                          "New dependency risk",
                          "Logging / audit / PII",
                          "Rate-limiting / abuse resistance",
                          "Findings — CRITICAL",
                          "Findings — HIGH / MEDIUM / LOW",
                          "Go / no-go for production"]},
        ],
    },
    "bf_test_plan": {
        "id": "bf_test_plan",
        "label": "7. Test plan (TDD)",
        "tagline": "Unit → integration → E2E → adversarial → regression. Consumes DeliveryPlan / ArchitectureDoc / PRD.",
        "expected_duration_s": 200,
        "accepts_source_types": ["DeliveryPlan", "ArchitectureDoc", "PRD"],
        "artifact_type": "TestPlan",
        "steps": [
            {"id": "plan",        "label": "Write the test plan (TDD)",      "type": "intent_turn", "intent": "bf_test_plan"},
            {"id": "coverage",    "label": "Identify coverage gaps",         "type": "intent_turn", "intent": "em_test_coverage"},
            {"id": "synth",       "label": "Compose TestPlan",               "type": "synthesize",
             "sections": ["Unit tests — name · pins · cost",
                          "Integration tests — real boundaries (no mocked DBs)",
                          "End-to-end tests — user-visible flow",
                          "Adversarial — invalid input, races, partial failure, idempotency",
                          "Regression pins — cheapest tests that catch ripple effects",
                          "Coverage gaps in existing code (priority order)",
                          "Minimum-bar subset for merge",
                          "Runtime budget per layer"]},
        ],
    },
}


# ---------- Run state I/O -------------------------------------------------

def _safe_id(rid: str) -> str:
    safe = "".join(c for c in rid if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid run id")
    return safe


def _run_path(ws: Workspace, run_id: str):
    ws.ensure_dirs()
    return ws.playbook_runs_dir / f"{_safe_id(run_id)}.json"


def _save_run(ws: Workspace, run: dict[str, Any]) -> None:
    _run_path(ws, run["id"]).write_text(json.dumps(run, indent=2))


def get_run(ws: Workspace, run_id: str) -> dict[str, Any] | None:
    p = _run_path(ws, run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_runs(ws: Workspace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not ws.playbook_runs_dir.exists():
        return out
    for p in sorted(ws.playbook_runs_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "id": r["id"],
            "playbook_id": r["playbook_id"],
            "playbook_label": r.get("playbook_label"),
            "status": r.get("status"),
            "current_step": r.get("current_step"),
            "step_count": len(r.get("steps", [])),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "final_artifact_id": r.get("final_artifact_id"),
            "scenario": r.get("user_inputs", {}).get("scenario", "")[:120],
        })
    return out


def delete_run(ws: Workspace, run_id: str) -> bool:
    p = _run_path(ws, run_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def cleanup_orphaned_runs() -> int:
    """On backend startup, mark any run still flagged running/queued as failed.

    The runner runs in daemon threads, so a backend restart kills in-flight
    work but leaves the JSON state stuck at 'running'. Sweep once at boot
    and surface the orphan clearly to the user as 'server restarted'.
    Returns the number of runs swept.
    """
    import workspaces as ws_store
    swept = 0
    for ws_summary in ws_store.list_workspaces():
        ws = ws_store.get_workspace(ws_summary["id"])
        if ws is None or not ws.playbook_runs_dir.exists():
            continue
        for p in ws.playbook_runs_dir.glob("*.json"):
            try:
                run = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if run.get("status") in ("running", "queued"):
                run["status"] = "failed"
                run["error"] = "Server restarted before run completed."
                run["finished_at"] = time.time()
                # Also mark any in-flight step as failed so the timeline reads sanely.
                for s in run.get("steps", []):
                    if s.get("status") in ("running", "queued"):
                        s["status"] = "failed"
                        s["finished_at"] = time.time()
                p.write_text(json.dumps(run, indent=2))
                swept += 1
    return swept


# ---------- Run execution -------------------------------------------------

def _merge_tokens(into: dict[str, int], add: dict[str, Any]) -> None:
    into["input"] = int(into.get("input", 0)) + int(add.get("input", 0) or 0)
    into["output"] = int(into.get("output", 0)) + int(add.get("output", 0) or 0)


# Single source of truth for what horizons the system understands. The simulate
# primitive supports a smaller subset; we fall back gracefully there.
HORIZONS: dict[str, str] = dict(foresight.HORIZONS)


def _valid_horizon(h: str | None) -> str:
    return h if h in HORIZONS else "1y"


SYNTH_INFERENCE_STRATEGIES = {"none", "reflection", "cove", "best_of_3"}


def _resolve_model(answer_model: str | None) -> str:
    """Pick the model for a playbook LLM call. User selection wins; otherwise
    fall back to GRAPHIFY_PLAYBOOK_MODEL, then Sonnet 4.6."""
    if answer_model and answer_model.strip():
        return answer_model.strip()
    return os.environ.get("GRAPHIFY_PLAYBOOK_MODEL", "claude-sonnet-4-6")


def _resolve_playbook(ws: Workspace, playbook_id: str) -> dict[str, Any] | None:
    """Find a playbook by id — user override wins, then workspace/global user
    store, then the built-in registry."""
    try:
        import playbook_store  # local import to avoid cycle
        user = playbook_store.get_playbook(ws, playbook_id)
        if user:
            return user
    except Exception:
        pass
    if playbook_id in PLAYBOOKS:
        return PLAYBOOKS[playbook_id]
    return None


def _resolve_run_steps(pb: dict[str, Any], fact_check: bool) -> list[dict[str, Any]]:
    """Build the steps list this run actually executes. Splices a fact-check
    step right before the SYNTH when the user enabled it on the kickoff form.
    """
    steps = [dict(s) for s in pb["steps"]]
    if fact_check:
        fc = {
            "id": "factcheck",
            "label": "Fact-check load-bearing claims",
            "type": "factcheck",
        }
        # Insert immediately before the SYNTH (last) step.
        steps.insert(-1, fc)
    return steps


def start_run(
    ws: Workspace,
    *,
    playbook_id: str,
    scenario: str,
    horizon: str = "1y",
    source_artifact_id: str | None = None,
    rubric_id: str | None = None,
    web_grounding: bool = True,
    synth_inference_strategy: str = "none",
    fact_check: bool = False,
    answer_model: str | None = None,
) -> dict[str, Any]:
    """Persist a queued run and spawn a background worker. Returns the run
    record immediately (status=queued); the worker updates it as it goes.
    """
    pb = _resolve_playbook(ws, playbook_id)
    if not pb:
        raise ValueError(f"Unknown playbook: {playbook_id}")

    resolved_steps = _resolve_run_steps(pb, fact_check)
    strategy = synth_inference_strategy if synth_inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"

    now = time.time()
    run = {
        "id": uuid.uuid4().hex[:12],
        "playbook_id": pb["id"],
        "playbook_label": pb["label"],
        "workspace_id": ws.id,
        "user_inputs": {
            "scenario": scenario.strip(),
            "horizon": _valid_horizon(horizon),
            "source_artifact_id": source_artifact_id,
            "rubric_id": rubric_id,
            "web_grounding": bool(web_grounding),
            "synth_inference_strategy": strategy,
            "fact_check": bool(fact_check),
            "answer_model": (answer_model or "").strip() or None,
        },
        # We persist the resolved step list as the source of truth for the
        # runner. If a future restart resumes this run we don't need to
        # re-derive it from the playbook template.
        "resolved_steps": resolved_steps,
        "status": "queued",
        "current_step": 0,
        "cancel_requested": False,
        "steps": [
            {
                "id": s["id"],
                "label": s["label"],
                "type": s["type"],
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "tokens": {"input": 0, "output": 0},
                "output": "",
                "web_sources": [],
            }
            for s in resolved_steps
        ],
        "total_tokens": {"input": 0, "output": 0},
        "started_at": now,
        "finished_at": None,
        "final_artifact_id": None,
        "error": None,
    }
    _save_run(ws, run)

    t = threading.Thread(target=_execute_run, args=(ws, run["id"]), daemon=True)
    t.start()
    return run


def resume_run(ws: Workspace, run_id: str) -> dict[str, Any] | None:
    """Resume a failed or cancelled run from the first non-complete step.
    Prior complete-step outputs are preserved and threaded into the resumed
    steps as context. Returns the (updated) run, or None if not found.
    Raises ValueError if the run is in a state that can't be resumed.
    """
    run = get_run(ws, run_id)
    if run is None:
        return None
    if run.get("status") in ("running", "queued"):
        raise ValueError("Run is still in flight; cancel it first.")
    if run.get("status") == "complete":
        raise ValueError("Run already completed; start a new run instead.")

    # Find the first step that isn't 'complete' — that's where we pick up.
    steps = run.get("steps", [])
    start_at = 0
    for i, s in enumerate(steps):
        if s.get("status") != "complete":
            start_at = i
            break
    else:
        # All steps look complete but the run isn't — anomaly; restart from end.
        start_at = max(0, len(steps) - 1)

    # Reset the resume target + everything after it to pending so the timeline
    # reads cleanly while the worker re-runs them.
    for i in range(start_at, len(steps)):
        steps[i]["status"] = "pending"
        steps[i]["started_at"] = None
        steps[i]["finished_at"] = None
        steps[i]["output"] = ""
        steps[i]["tokens"] = {"input": 0, "output": 0}
        steps[i]["web_sources"] = []

    run["status"] = "queued"
    run["cancel_requested"] = False
    run["error"] = None
    run["finished_at"] = None
    run["current_step"] = start_at
    # Don't reset total_tokens — they reflect what's already been spent.
    _save_run(ws, run)

    t = threading.Thread(target=_execute_run, args=(ws, run_id), kwargs={"start_at_step": start_at}, daemon=True)
    t.start()
    return run


def _check_cancelled(ws: Workspace, run_id: str) -> bool:
    """Re-read the run from disk to pick up an out-of-band cancel request."""
    latest = get_run(ws, run_id)
    return bool(latest and latest.get("cancel_requested"))


def request_cancel(ws: Workspace, run_id: str) -> dict[str, Any] | None:
    """Flip the run's cancel_requested flag. The worker thread sees it
    between steps and stops cleanly."""
    run = get_run(ws, run_id)
    if run is None:
        return None
    if run.get("status") not in ("running", "queued"):
        # Already terminal — nothing to cancel.
        return run
    run["cancel_requested"] = True
    _save_run(ws, run)
    return run


def _execute_run(ws: Workspace, run_id: str, *, start_at_step: int = 0) -> None:
    """Background worker. Mutates the run record as each step finishes.
    `start_at_step` lets a Resume reuse prior step outputs and pick up from
    the first non-complete step.
    """
    try:
        run = get_run(ws, run_id)
        if not run:
            return
        pb = _resolve_playbook(ws, run["playbook_id"])
        if pb is None:
            run["status"] = "failed"
            run["error"] = f"Unknown playbook (was it deleted?): {run['playbook_id']}"
            run["finished_at"] = time.time()
            _save_run(ws, run)
            return
        run["status"] = "running"
        run["cancel_requested"] = False
        _save_run(ws, run)

        scenario = run["user_inputs"]["scenario"]
        rubric_id = run["user_inputs"].get("rubric_id")
        web_grounding = bool(run["user_inputs"].get("web_grounding", True))
        source_art_id = run["user_inputs"].get("source_artifact_id")
        run_horizon = _valid_horizon(run["user_inputs"].get("horizon"))
        synth_strategy = run["user_inputs"].get("synth_inference_strategy") or "none"
        answer_model = run["user_inputs"].get("answer_model") or None
        # Pre-existing runs (saved before this field was added) won't have
        # resolved_steps — fall back to the playbook template.
        resolved_steps = run.get("resolved_steps") or pb["steps"]

        rubric_body = ""
        if rubric_id:
            r = rubric_store.get_rubric(ws, rubric_id)
            if r:
                rubric_body = r.get("body", "")

        mem_block = memory_store.memory_block(ws)

        # If we're seeded from a prior artifact, render it as context for step 1.
        prior_context = ""
        if source_art_id:
            src = artifacts.get_artifact(ws, source_art_id)
            if src:
                prior_context = artifacts.render_for_prompt(src)

        # Accumulated outputs flow into each subsequent step. On Resume, seed
        # from prior complete steps so we don't re-run them.
        step_outputs: list[str] = []
        all_web_sources: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        if start_at_step > 0:
            for i in range(start_at_step):
                prev = run["steps"][i]
                if prev.get("output"):
                    step_outputs.append(f"### {resolved_steps[i]['label']}\n{prev['output']}")
                for s in prev.get("web_sources", []) or []:
                    u = s.get("url")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        all_web_sources.append(s)

        for idx, step_def in enumerate(resolved_steps):
            if idx < start_at_step:
                continue  # already complete from a prior run; outputs already seeded above
            if _check_cancelled(ws, run_id):
                run = get_run(ws, run_id) or run
                run["status"] = "cancelled"
                run["finished_at"] = time.time()
                _save_run(ws, run)
                return
            run["current_step"] = idx
            run["steps"][idx]["status"] = "running"
            run["steps"][idx]["started_at"] = time.time()
            _save_run(ws, run)

            try:
                step_type = step_def["type"]
                # Step-level horizon overrides the run horizon (rare, but supported).
                step_horizon = _valid_horizon(step_def.get("horizon") or run_horizon)
                if step_type == "intent_turn":
                    out_text, tokens, web = _run_intent_step(
                        ws, step_def, scenario, prior_context, step_outputs,
                        rubric_body, mem_block, web_grounding, step_horizon,
                        answer_model,
                    )
                elif step_type == "divergent":
                    out_text, tokens, web = _run_divergent_step(
                        ws, step_def, scenario, prior_context, step_outputs,
                        mem_block, step_horizon, answer_model,
                    )
                elif step_type == "foresight":
                    out_text, tokens, web = _run_foresight_step(
                        ws, run["id"], step_def, scenario, prior_context, step_outputs,
                        rubric_body, mem_block, web_grounding, step_horizon,
                        answer_model,
                    )
                elif step_type == "simulate":
                    out_text, tokens, web = _run_simulate_step(
                        ws, step_def, scenario, prior_context, step_outputs,
                        rubric_body, mem_block, web_grounding, step_horizon,
                    )
                elif step_type == "factcheck":
                    out_text, tokens, web = _run_factcheck_step(
                        ws, step_def, scenario, prior_context, step_outputs,
                        rubric_body, mem_block, answer_model,
                    )
                elif step_type == "synthesize":
                    out_text, tokens, web = _run_synth_step(
                        ws, pb, step_def, scenario, prior_context, step_outputs,
                        rubric_body, mem_block, web_grounding, step_horizon,
                        synth_strategy, answer_model,
                    )
                else:
                    raise ValueError(f"Unknown step type: {step_type}")
            except Exception as exc:  # noqa: BLE001
                from graphify_runner import humanize_anthropic_error as _human
                human_msg = _human(exc)
                run["steps"][idx]["status"] = "failed"
                run["steps"][idx]["finished_at"] = time.time()
                run["steps"][idx]["output"] = f"Step failed: {human_msg}\n\n{traceback.format_exc()}"
                run["status"] = "failed"
                run["error"] = human_msg
                run["finished_at"] = time.time()
                _save_run(ws, run)
                return

            run["steps"][idx]["output"] = out_text
            run["steps"][idx]["tokens"] = tokens
            run["steps"][idx]["web_sources"] = web
            run["steps"][idx]["status"] = "complete"
            run["steps"][idx]["finished_at"] = time.time()
            _merge_tokens(run["total_tokens"], tokens)
            for s in web:
                u = s.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_web_sources.append(s)
            step_outputs.append(f"### {step_def['label']}\n{out_text}")
            _save_run(ws, run)

            # The final step is `synthesize` — extract the artifact from it.
            if step_def["type"] == "synthesize":
                art = _emit_artifact(ws, pb, run, out_text, all_web_sources, source_art_id)
                run["final_artifact_id"] = art["id"]
                _save_run(ws, run)

        run["status"] = "complete"
        run["finished_at"] = time.time()
        run["web_sources"] = all_web_sources
        _save_run(ws, run)
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        try:
            run = get_run(ws, run_id) or {}
            run["status"] = "failed"
            run["error"] = f"{type(exc).__name__}: {exc}"
            run["finished_at"] = time.time()
            _save_run(ws, run)
        except Exception:
            pass


# ---------- Step kinds ----------------------------------------------------

def _step_question(
    scenario: str, prior_context: str, step_outputs: list[str], step_label: str,
    horizon: str = "1y",
) -> str:
    """Compose the question text for an intent step, threading prior outputs +
    the time horizon in so the model reasons in the right time frame."""
    horizon_phrase = HORIZONS.get(horizon, horizon)
    parts = [
        f"User scenario: {scenario}",
        f"Time horizon for this analysis: {horizon_phrase}",
    ]
    if prior_context:
        parts.append(prior_context)
    if step_outputs:
        parts.append("Earlier in this playbook:\n\n" + "\n\n---\n\n".join(step_outputs))
    parts.append(f"Now: {step_label}.")
    return "\n\n".join(parts)


def _run_intent_step(
    ws: Workspace, step_def: dict[str, Any], scenario: str, prior_context: str,
    step_outputs: list[str], rubric_body: str, mem_block: str, web_grounding: bool,
    horizon: str, answer_model: str | None = None,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    intent_text = rubric_store.intent_instruction(step_def.get("intent"), ws)
    question = _step_question(scenario, prior_context, step_outputs, step_def["label"], horizon)
    result = rich_query(
        ws, question,
        intent_instruction=intent_text,
        rubric_body=rubric_body,
        memory_block=mem_block,
        inference_strategy="none",
        web_grounding=web_grounding,
        answer_model=answer_model,
    )
    return (
        result.get("answer", ""),
        result.get("answer_tokens", {}) or {},
        result.get("web_sources", []) or [],
    )


def _run_divergent_step(
    ws: Workspace, step_def: dict[str, Any], scenario: str, prior_context: str,
    step_outputs: list[str], mem_block: str, horizon: str,
    answer_model: str | None = None,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """Divergent ideation. Deliberately bypasses the three biggest sources of
    convergence in the pipeline:

      1. No graph context — the corpus subgraph is NOT prepended, so the model
         doesn't anchor on existing themes.
      2. No rubric — strategic guardrails (capital constraints, Sherlocking,
         distribution) are stripped so they don't pre-filter wild candidates.
      3. High temperature (1.0) — the SDK default is conservative.

    Memory is still applied because it's user/team identity (preferences, who
    we are), not corpus bias. Prior step outputs, if any, are framed as
    'themes to AVOID re-treading' rather than scaffold.

    Step-level overrides (set in the playbook step definition) — all optional:
      - count: how many ideas to demand (default 20)
      - temperature: 0.0-1.0 (default 1.0)
      - max_tokens: int (default 8000)
    """
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured (ANTHROPIC_API_KEY missing)")

    count = int(step_def.get("count", 20))
    temperature = float(step_def.get("temperature", 1.0))
    max_tokens = int(step_def.get("max_tokens", 8000))
    horizon_phrase = HORIZONS.get(horizon, horizon)

    system = (
        "You are a creative product strategist hunting for genuinely new ideas. "
        "You are EXPLICITLY NOT constrained by the company's current product "
        "line, existing customer base, distribution channels, or strategic "
        "preferences. Your job at this stage is divergent ideation — quantity "
        "over quality, novelty over feasibility.\n\n"
        f"Produce {count} candidate product ideas. Rules:\n"
        f"- At least HALF must be in product categories the company doesn't "
        f"currently play in.\n"
        "- Include at least 3 ideas borrowed from analogies to UNRELATED "
        "industries (biology, military, luxury, gaming, education, biotech, "
        "finance, urban planning, etc.). Name the analogue explicitly.\n"
        "- Reserve 2-3 slots for 'heretical' ideas that would violate the "
        "company's stated strategic assumptions if pursued.\n"
        "- Do NOT filter, rank, or self-censor at this stage. Downstream steps "
        "will evaluate and narrow.\n"
        "- Do NOT cite the corpus or existing internal artifacts. Treat the "
        "scenario as a launch pad, not a constraint.\n\n"
        "Format: numbered list (1, 2, 3, …). For each idea, exactly three lines:\n"
        "  - **Title:** short, evocative\n"
        "  - **What it is:** one sentence describing the product\n"
        "  - **Non-obvious angle:** one sentence on why this isn't what an "
        "incumbent would build, OR which unrelated industry inspired it"
    )
    if mem_block:
        system = mem_block + "\n\n" + system

    user_parts = [
        f"Strategic horizon: {horizon_phrase}",
        f"Scenario: {scenario}",
    ]
    if prior_context:
        # Source artifact is *context* but not a constraint — frame it that way.
        user_parts.append(
            "Context (use as inspiration, do NOT let it anchor you):\n\n" + prior_context
        )
    if step_outputs:
        # Earlier steps in this playbook were likely corpus-anchored. Frame
        # them as the SPACE TO AVOID so divergence is forced outward.
        user_parts.append(
            "Themes already well-covered — AVOID re-treading these; aim "
            "elsewhere:\n\n" + "\n\n---\n\n".join(step_outputs)
        )
    user_parts.append(
        f"Now: produce {count} candidate ideas. Bias toward novelty over "
        "feasibility. Downstream steps will pressure-test the survivors."
    )
    user = "\n\n".join(user_parts)

    model = _resolve_model(answer_model)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()

    text_parts: list[str] = [
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    ]
    raw = "".join(text_parts).strip()
    usage = getattr(msg, "usage", None)
    return (
        raw,
        {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        },
        [],  # no web sources for divergent step
    )


def _run_foresight_step(
    ws: Workspace, run_id: str, step_def: dict[str, Any], scenario: str,
    prior_context: str, step_outputs: list[str], rubric_body: str,
    mem_block: str, web_grounding: bool, horizon: str,
    answer_model: str | None = None,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """Create a real foresight session for this step and run it. The session is
    persisted under foresight/ so it surfaces in the ForeSight tab too.
    """
    persona_ids = [p for p in step_def.get("personas", []) if foresight.get_persona(p)]
    if not persona_ids:
        raise ValueError("foresight step has no valid personas")

    # Build the scenario string with prior context folded in.
    scenario_text = scenario
    extras: list[str] = []
    if prior_context:
        extras.append(prior_context)
    if step_outputs:
        extras.append("Findings so far:\n\n" + "\n\n".join(step_outputs))
    if extras:
        scenario_text = scenario + "\n\n" + "\n\n".join(extras)

    session = foresight.create_session(
        ws,
        title=f"Playbook step: {step_def['label']}",
        scenario=scenario_text,
        horizon=horizon,
        persona_ids=persona_ids,
        rounds=int(step_def.get("rounds", 1)),
        rubric_id=None,
        use_graph=True,
        use_memory=True,
        web_grounding=web_grounding,
        synth_inference_strategy="none",
        source_conversation_id=None,
        source_conversation_title=f"playbook-run:{run_id}",
        answer_model=answer_model,
    )
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured (ANTHROPIC_API_KEY missing)")

    def _graph_ctx(q: str) -> dict[str, Any]:
        try:
            return render_graph_context(ws, q)
        except Exception:
            return {"rendered": "", "entry_node_labels": []}

    finished = foresight.run_session(
        ws, session["id"], client,
        graph_context_fn=_graph_ctx,
        rubric_body=rubric_body,
        memory_block=mem_block,
        conversation_history="",
        intent_instruction="",
    )
    out = finished.get("output") or {}
    return (
        out.get("synthesis", ""),
        out.get("tokens", {}) or {},
        out.get("web_sources", []) or [],
    )


def _run_simulate_step(
    ws: Workspace, step_def: dict[str, Any], scenario: str, prior_context: str,
    step_outputs: list[str], rubric_body: str, mem_block: str, web_grounding: bool,
    horizon: str,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """Quick simulate: 4 fixed personas in parallel + a synthesizer. ~30-60s.
    Faster + cheaper than foresight; the right pick when you want a multi-
    perspective read without persona-position-update tracking across rounds.
    """
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured")

    persona_keys = step_def.get("personas") or ["bull", "bear", "customer", "competitor"]
    persona_keys = [k for k in persona_keys if k in sim_store.PERSONAS]
    if not persona_keys:
        raise ValueError("simulate step has no valid personas")

    # Compose a question that threads prior context in.
    question_parts = [scenario]
    if prior_context:
        question_parts.append(prior_context)
    if step_outputs:
        question_parts.append("Findings so far:\n\n" + "\n\n".join(step_outputs))
    question = "\n\n".join(question_parts)

    try:
        graph_ctx = render_graph_context(ws, scenario)
    except Exception:
        graph_ctx = {"rendered": "", "entry_node_labels": []}

    # simulate.py supports a subset of horizons (6mo/1y/3y); fall back to 1y
    # for the others rather than passing through an unknown key.
    sim_horizon = horizon if horizon in sim_store.HORIZONS else "1y"
    result = sim_store.run_simulation(
        client,
        question=question,
        horizon=sim_horizon,
        graph_context=graph_ctx.get("rendered", ""),
        history_text="",
        rubric_body=rubric_body,
        persona_keys=persona_keys,
        memory_block=mem_block,
        web_grounding=web_grounding,
    )

    # Render the simulate result as markdown so it slots cleanly into step_outputs.
    persona_blocks = "\n\n".join(
        f"### {p['label']}\n{p.get('text', '')}" for p in result.get("personas", [])
    )
    rendered = (
        f"**Quick simulate — {len(result.get('personas', []))} personas (no debate rounds)**\n\n"
        f"{persona_blocks}\n\n"
        f"## Synthesis\n{result.get('synthesis', '')}"
    )
    return (
        rendered,
        result.get("tokens", {}) or {},
        result.get("web_sources", []) or [],
    )


def _run_factcheck_step(
    ws: Workspace, step_def: dict[str, Any], scenario: str, prior_context: str,
    step_outputs: list[str], rubric_body: str, mem_block: str,
    answer_model: str | None = None,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """Extract load-bearing claims from prior step outputs and verify each via
    web_search. Emit a Markdown table classifying each claim. The SYNTH step
    that follows is instructed to weight verified claims and hedge on the rest.
    """
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured")
    if not step_outputs:
        return ("(no prior step outputs to fact-check)", {"input": 0, "output": 0}, [])

    transcript = "\n\n---\n\n".join(step_outputs)
    system = (
        "You are a research fact-checker. Read the prior step outputs of a "
        "playbook and extract 5-10 atomic, falsifiable claims (facts, dates, "
        "numbers, named events, regulatory deadlines, named companies/products). "
        "Skip subjective claims ('we should…', 'this is promising').\n\n"
        "For each claim, use the web_search tool to verify it. Then classify:\n"
        "  • VERIFIED — web sources confirm the claim\n"
        "  • PARTIALLY-VERIFIED — confirmed in part; flag what's unsupported\n"
        "  • UNVERIFIED — no web evidence found (model judgment / inference only)\n"
        "  • CONTRADICTED — web sources actively disagree\n"
        "  • TIME-SENSITIVE — depends on current state that may have changed\n\n"
        "Output ONLY a Markdown table with this exact header and no preamble:\n\n"
        "| Claim | Status | Source | Note |\n"
        "|---|---|---|---|\n\n"
        "Source column: cite the corpus file (e.g. 'file.pdf') for graph-grounded "
        "claims, or 'web: domain.com' for web-verified ones. Note column: 1 short "
        "sentence — what the verification turned up, or what's missing."
    )
    if rubric_body:
        system += "\n\nApply these framing rules when judging materiality:\n" + rubric_body
    if mem_block:
        system = mem_block + "\n\n" + system

    user = (
        f"Scenario: {scenario}\n\n"
        + (f"{prior_context}\n\n" if prior_context else "")
        + f"Prior step outputs to fact-check:\n\n{transcript}\n\n"
        + "Extract and verify the most load-bearing claims now."
    )

    model = _resolve_model(answer_model)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 3500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_FACTCHECK_WEB_MAX_USES", "8")),
        }],
    }
    msg = client.messages.create(**kwargs)
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
    raw = "".join(text_parts).strip()
    usage = getattr(msg, "usage", None)
    return (
        raw,
        {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        },
        web_sources,
    )


def _merge_tokens(*tokens: dict[str, Any] | None) -> dict[str, int]:
    total = {"input": 0, "output": 0}
    for t in tokens:
        if not t:
            continue
        total["input"] += int(t.get("input", 0) or 0)
        total["output"] += int(t.get("output", 0) or 0)
    return total


def _dedupe_sources(*lists: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for lst in lists:
        for s in lst or []:
            u = s.get("url")
            if u and u not in seen:
                seen.add(u)
                out.append(s)
    return out


_JSON_RE = re.compile(r"\{[\s\S]+\}")


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text


def _synth_llm_call(
    client: Any, model: str, system: str, user: str, web_grounding: bool,
    max_tokens: int = 16000,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """One Claude call for synth-class work. Returns (text, tokens, web_sources).

    Streams the response — the SDK refuses non-streaming requests whose
    max_tokens implies a potential >10-minute generation.

    Two timeout/sizing knobs that matter here:

    1. `max_tokens` defaults to 16k (overridable per call). The synth system
       prompt enforces tight section bodies (3-5 bullets OR ≤4 sentences),
       so a 17-section brief lands well under 8k tokens. 16k gives headroom
       without inviting verbose drift. Older default was 64k which let one
       call's worst-case generation exceed the connection's idle budget.

    2. We pin a per-call timeout via `client.with_options(timeout=…)` instead
       of relying on the global client default (90s). The global default is
       tight on purpose — small calls shouldn't tolerate slow responses — but
       synth calls genuinely stream JSON for minutes and need 20-30 minutes
       of headroom. Without this override the SDK retries on the 90s timeout,
       producing the classic "failed after ~6 min" symptom (90s × 1 + 3
       retries = 360s).
    """
    synth_timeout = float(os.environ.get("ANTHROPIC_SYNTH_TIMEOUT_S", "1800"))
    sclient = client.with_options(timeout=synth_timeout, max_retries=1)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_grounding:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(os.environ.get("GRAPHIFY_WEB_MAX_USES", "3")),
        }]
    with sclient.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
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
    raw = "".join(text_parts).strip()
    usage = getattr(msg, "usage", None)
    tokens = {
        "input": getattr(usage, "input_tokens", 0) if usage else 0,
        "output": getattr(usage, "output_tokens", 0) if usage else 0,
    }
    return raw, tokens, web_sources


def _run_synth_step(
    ws: Workspace, pb: dict[str, Any], step_def: dict[str, Any], scenario: str,
    prior_context: str, step_outputs: list[str], rubric_body: str,
    mem_block: str, web_grounding: bool, horizon: str,
    inference_strategy: str = "none",
    answer_model: str | None = None,
) -> tuple[str, dict[str, int], list[dict[str, str]]]:
    """The final pass: compose a structured artifact (JSON) from every prior
    step's output. Optionally wrap the draft in an inference strategy
    (reflection / cove / best_of_3) to harden the final brief.
    """
    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured")

    sections = step_def.get("sections", [])
    sections_clause = "\n".join(f'  - "{s}"' for s in sections)
    transcript = "\n\n---\n\n".join(step_outputs)

    # Detect whether the prior steps include a fact-check; if so, prime the
    # SYNTH to weight by claim status.
    factcheck_present = any("Status |" in s and "Source |" in s for s in step_outputs)

    base_system = (
        "You compose an executive brief from the outputs of a multi-step playbook.\n\n"
        "House style — CLEAR, CONCISE, COHERENT, COMPLETE:\n"
        "- Be concrete, opinionated, and brief. No preamble, no filler ('it's worth noting').\n"
        "- TL;DR ≤ 30 words. One sentence — the single most important takeaway.\n"
        "- Section bodies: 3–5 bullets OR ≤ 4 short sentences. Never both.\n"
        "- One idea per bullet, ≤ 22 words. Numbers and names over generalities.\n"
        "- Cite source_file in parens when the underlying step did.\n"
        "PRESERVE — never drop load-bearing detail from the step outputs:\n"
        "- Every dollar amount, percentage, ratio, date, deadline.\n"
        "- Every named entity (company, product, person, regulation).\n"
        "- Every source_file citation and web attribution.\n"
        "- Every kill gate, verdict, and recommendation.\n"
        "If brevity conflicts with a load-bearing fact, KEEP THE FACT and trim prose instead.\n\n"
        "Output STRICT JSON with no preamble and no code fences. Shape:\n"
        '{\n'
        '  "title": "short, specific title",\n'
        '  "tldr": "≤ 30 words — the single most important takeaway",\n'
        '  "highlights": [\n'
        '    { "text": "punchy one-liner ≤ 22 words", "tone": "win" | "risk" | "claim" | "tension" | "number" },\n'
        '    ...\n'
        '  ],\n'
        '  "sections": {\n'
        '    "<section_name>": "<3–5 bullets OR ≤ 4 short sentences>",\n'
        '    ...\n'
        '  }\n'
        '}\n\n'
        f"The `sections` keys MUST be exactly these (in this order):\n{sections_clause}\n\n"
        "`highlights` rules:\n"
        "- 3–5 items, each a quotable one-liner pulled from across the sections.\n"
        "- Mix tones — at least one `win`, one `risk`, and either a `number` (load-bearing "
        "metric e.g. 'ARR $30M over 24 months') or a `tension` (strongest persona disagreement).\n"
        "- `claim` = a standout statement that doesn't fit win/risk/number/tension.\n"
        "- Skim test: a reader who reads ONLY these 3–5 lines should know what the brief argues."
    )
    if factcheck_present:
        base_system += (
            "\n\nA fact-check step is included in the prior outputs. Weight VERIFIED "
            "claims heavily. For CONTRADICTED claims, exclude or invert them. For "
            "UNVERIFIED claims, hedge with phrases like 'reportedly' / 'unverified'. "
            "Surface this calibration in the TL;DR where it matters."
        )
    # Detect whether the prior steps include a divergent ideation step. When
    # they do, the synth must treat the rubric as signal — not as a filter —
    # so strong-but-divergent ideas survive instead of being silently dropped.
    divergent_present = any(
        "Non-obvious angle" in s or "heretical" in s.lower() for s in step_outputs
    )

    if rubric_body:
        if divergent_present:
            base_system += (
                "\n\n## Framing rules (apply as a LENS, not a filter)\n"
                f"{rubric_body}\n\n"
                "## How to sort divergent ideas\n"
                "A divergent step ran upstream and produced ~15-20 wild "
                "candidate ideas. Sort EVERY candidate idea into ONE of three "
                "tiers — DO NOT silently drop any of them. The human reviewer "
                "needs to see the full landscape, including the long tail.\n\n"
                "The three bars an idea must clear to be 'Tier 1':\n"
                "  1. **Strong demand signal** — concrete buyer with a real, "
                "monetizable pain. Not 'someone might want this'.\n"
                "  2. **Appfire can pull it through** — credible execution "
                "path using Appfire's actual assets: Marketplace distribution, "
                "Jira/Confluence customer base, JMWE/BigPicture/Comala/"
                "Pluralsight Flow product surface, channel sales, or the "
                "team's known domain depth. Name the lever explicitly.\n"
                "  3. **Defensible differentiation** — at least one structural "
                "reason this is hard for an incumbent (Atlassian, the named "
                "competitors) to copy in 12 months.\n\n"
                "Tier rules:\n"
                "  - **Tier 1 — Top opportunities (ranked):** clears ALL three "
                "bars AND respects the framing rules. These lead the brief.\n"
                "  - **Tier 2 — Rubric tensions:** clears all three bars BUT "
                "violates the framing rules. Tag the rule(s) it breaks and "
                "propose the rubric update it'd justify (e.g. 'expand capital "
                "ceiling to $250M', 'lift the Sherlocking bar where "
                "regulatory moat insulates against bundling'). These are the "
                "most valuable output — evidence strong enough to bend the "
                "rubric.\n"
                "  - **Tier 3 — Candidates for human review:** doesn't clear "
                "all three bars (weak demand signal, no clear Appfire lever, "
                "or no obvious moat) BUT is still interesting enough that a "
                "human should glance at it. For each: one sentence on the "
                "idea, one sentence on which bar it fails, one sentence on "
                "what would have to be true for it to graduate to Tier 1 or 2.\n\n"
                "Every divergent idea must land in one tier. Nothing is "
                "discarded — the brief is the audit trail."
            )
        else:
            base_system += f"\n\nApply these framing rules:\n{rubric_body}"
    if mem_block:
        base_system = mem_block + "\n\n" + base_system

    horizon_phrase = HORIZONS.get(horizon, horizon)
    base_user = (
        f"Original user scenario:\n{scenario}\n\n"
        f"Time horizon: {horizon_phrase}\n\n"
        + (f"{prior_context}\n\n" if prior_context else "")
        + f"All step outputs:\n\n{transcript}\n\n"
        + "Compose the JSON brief now."
    )

    model = _resolve_model(answer_model)
    strategy = inference_strategy if inference_strategy in SYNTH_INFERENCE_STRATEGIES else "none"

    # Helper: every inference strategy below makes the FIRST call the load-
    # bearing one (the draft / a candidate). Subsequent calls can fail
    # (timeout, dropped connection, transient 5xx) without invalidating the
    # whole synth — we'd rather ship a usable draft than fail a 12-minute
    # playbook over a flaky verification pass.
    def _try(label: str, fn):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            print(f"[playbooks] synth {strategy}/{label} failed, falling back: {exc}", flush=True)
            return None, str(exc)

    if strategy == "none":
        return _synth_llm_call(client, model, base_system, base_user, web_grounding)

    if strategy == "reflection":
        # Draft must succeed — if it doesn't there's nothing to fall back to.
        draft_raw, draft_tok, draft_web = _synth_llm_call(client, model, base_system, base_user, web_grounding)
        critique_system = "You are a careful reviewer of executive briefs. Be specific and brief."
        critique_user = (
            f"Below is a draft JSON brief for the user's scenario. Critique it:\n"
            f"- What's weak, missing, or unsupported?\n"
            f"- Are TL;DR + sections internally consistent?\n"
            f"- Any section that's filler or generic?\n"
            f"- Citations: any claim missing source_file/web that needs one?\n\n"
            f"Scenario: {scenario}\n\n"
            f"Draft:\n{draft_raw}\n\n"
            f"Return 3-5 bullets, then a single line 'REVISE: yes' or 'REVISE: no'."
        )
        crit_result, _ = _try("critique", lambda: _synth_llm_call(
            client, model, critique_system, critique_user, web_grounding=False, max_tokens=800,
        ))
        if not crit_result:
            return draft_raw, draft_tok, draft_web
        crit_raw, crit_tok, _crit_web = crit_result
        if "revise: no" in crit_raw.lower():
            return draft_raw, _merge_tokens(draft_tok, crit_tok), draft_web
        revise_user = (
            f"Revise the brief to address the critique. Keep the same JSON shape "
            f"and the same section keys.\n\n"
            f"Original brief:\n{draft_raw}\n\n"
            f"Critique:\n{crit_raw}\n\n"
            f"Revised brief (JSON only):"
        )
        rev_result, _ = _try("revise", lambda: _synth_llm_call(client, model, base_system, revise_user, web_grounding=False))
        if not rev_result:
            return draft_raw, _merge_tokens(draft_tok, crit_tok), draft_web
        rev_raw, rev_tok, rev_web = rev_result
        return rev_raw or draft_raw, _merge_tokens(draft_tok, crit_tok, rev_tok), _dedupe_sources(draft_web, rev_web)

    if strategy == "cove":
        # Draft must succeed.
        draft_raw, draft_tok, draft_web = _synth_llm_call(client, model, base_system, base_user, web_grounding)
        verify_user = (
            f"Generate 4-6 factual verification questions targeting specific claims "
            f"in the draft below. Then answer each one citing the transcript or "
            f"marking 'uncertain — would need external verification'.\n\n"
            f"Draft:\n{draft_raw}\n\n"
            f"Format: Q1: ...\nA1: ...\nQ2: ..."
        )
        ver_result, _ = _try("verify", lambda: _synth_llm_call(client, model, base_system, verify_user, web_grounding))
        if not ver_result:
            return draft_raw, draft_tok, draft_web
        ver_raw, ver_tok, ver_web = ver_result
        revise_user = (
            f"Revise the brief using the verification answers. Remove or hedge claims "
            f"that didn't verify. Keep the same JSON shape and section keys.\n\n"
            f"Original:\n{draft_raw}\n\n"
            f"Verification:\n{ver_raw}\n\n"
            f"Revised brief (JSON only):"
        )
        rev_result, _ = _try("revise", lambda: _synth_llm_call(client, model, base_system, revise_user, web_grounding=False))
        if not rev_result:
            return draft_raw, _merge_tokens(draft_tok, ver_tok), _dedupe_sources(draft_web, ver_web)
        rev_raw, rev_tok, rev_web = rev_result
        return (
            rev_raw or draft_raw,
            _merge_tokens(draft_tok, ver_tok, rev_tok),
            _dedupe_sources(draft_web, ver_web, rev_web),
        )

    # best_of_3 — at least ONE candidate must succeed. Soldier through partial
    # failures rather than throwing.
    candidates: list[tuple[str, dict[str, int], list[dict[str, str]]]] = []
    for i in range(3):
        cand, _ = _try(f"cand{i+1}", lambda: _synth_llm_call(client, model, base_system, base_user, web_grounding))
        if cand:
            candidates.append(cand)
    if not candidates:
        # All three failed — re-raise on the next attempt to surface the error.
        return _synth_llm_call(client, model, base_system, base_user, web_grounding)
    if len(candidates) == 1:
        return candidates[0]
    joined = "\n\n".join(f"### Candidate {i+1}\n{c[0]}" for i, c in enumerate(candidates))
    pick_user = (
        f"{len(candidates)} candidate JSON briefs of the same playbook are below. "
        f"Pick the BEST one based on: factual fidelity to the transcript, concrete "
        f"recommendations, sharpness of TL;DR, and consistent section quality.\n\n"
        f"{joined}\n\n"
        f"Output ONLY the chosen candidate's JSON (no preamble, no 'Candidate X')."
    )
    pick_result, _ = _try("pick", lambda: _synth_llm_call(client, model, base_system, pick_user, web_grounding=False))
    cand_toks = [c[1] for c in candidates]
    cand_webs = [c[2] for c in candidates]
    if not pick_result:
        # Picker failed — return the first candidate as a defensible default.
        return candidates[0][0], _merge_tokens(*cand_toks), _dedupe_sources(*cand_webs)
    pick_raw, pick_tok, pick_web = pick_result
    return (
        pick_raw or candidates[0][0],
        _merge_tokens(*cand_toks, pick_tok),
        _dedupe_sources(*cand_webs, pick_web),
    )


def _repair_truncated_json(text: str) -> str | None:
    """Walk a partial JSON object, find the last safe cut point (just after a
    completed value at depth ≥ 1), and close any unclosed structures. Returns
    the repaired JSON string or None if no repairable prefix exists.

    Handles the common failure mode where the model hits max_tokens mid-value.
    Whole key-value pairs that finished before truncation are preserved; the
    partial pair at the tail is dropped.
    """
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    n = len(s)

    in_str = False
    esc = False
    stack: list[str] = []        # expected closers
    expecting_value = False      # True right after `:` or inside an array
    last_safe_end = -1
    last_safe_stack: list[str] = []

    def _mark_safe(end_idx: int) -> None:
        nonlocal last_safe_end, last_safe_stack, expecting_value
        if stack:
            last_safe_end = end_idx
            last_safe_stack = list(stack)
        expecting_value = False

    i = 0
    while i < n:
        c = s[i]
        if esc:
            esc = False
            i += 1
            continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                if expecting_value:
                    _mark_safe(i + 1)
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "{":
            stack.append("}")
            expecting_value = False
            i += 1
            continue
        if c == "[":
            stack.append("]")
            expecting_value = True
            i += 1
            continue
        if c in "}]":
            if not stack or stack[-1] != c:
                break
            stack.pop()
            if not stack:
                rest = s[i + 1 :].strip()
                if not rest or not rest.startswith("{"):
                    return s[: i + 1]
            else:
                last_safe_end = i + 1
                last_safe_stack = list(stack)
            i += 1
            continue
        if c == ",":
            expecting_value = bool(stack) and stack[-1] == "]"
            i += 1
            continue
        if c == ":":
            expecting_value = True
            i += 1
            continue
        # Primitive (number / true / false / null) — consume until non-primitive.
        j = i
        while j < n and s[j] not in '{}[],:"' and not s[j].isspace():
            j += 1
        if j == n:
            break  # primitive truncated
        if expecting_value:
            _mark_safe(j)
        i = j

    if not stack and not in_str and i == n:
        return s  # already balanced

    if last_safe_end <= 0:
        return None

    repaired = s[:last_safe_end].rstrip()
    if repaired.endswith(","):
        repaired = repaired[:-1].rstrip()
    for closer in reversed(last_safe_stack):
        repaired += closer
    return repaired


def _parse_synth(raw: str, expected_sections: list[str]) -> dict[str, Any]:
    """Parse the synth JSON output into {title, tldr, sections}. Tolerant of
    stray prose around the JSON object and of truncated responses (model hit
    max_tokens mid-output)."""
    stripped = _strip_code_fences(raw)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Fallback 1: grab the first {...} block via greedy regex.
    m = _JSON_RE.search(stripped)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Fallback 2: walk the partial JSON, cut at the last safe point, close
    # open structures, and re-parse. Marks the result so callers can surface
    # that the brief was salvaged from a truncated response.
    repaired = _repair_truncated_json(stripped)
    if repaired:
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                parsed["_parse_repaired"] = True
                return parsed
        except json.JSONDecodeError:
            pass
    # Last resort: synthesize a minimal artifact from the raw text.
    return {
        "title": "Brief",
        "tldr": (stripped.splitlines() or [""])[0][:200],
        "highlights": [],
        "sections": {s: "" for s in expected_sections},
        "_parse_error": True,
    }


def _clean_highlights(raw: Any) -> list[dict[str, str]]:
    """Normalize the synth's `highlights` field into a list of {text, tone}.
    Tolerant of missing/extra keys and stray values."""
    valid_tones = {"win", "risk", "claim", "tension", "number"}
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for h in raw[:6]:  # cap at 6 in case the model goes overboard
        if isinstance(h, dict):
            text = str(h.get("text") or "").strip()
            tone = str(h.get("tone") or "claim").strip().lower()
        elif isinstance(h, str):
            text, tone = h.strip(), "claim"
        else:
            continue
        if not text:
            continue
        if tone not in valid_tones:
            tone = "claim"
        out.append({"text": text, "tone": tone})
    return out


def _emit_artifact(
    ws: Workspace, pb: dict[str, Any], run: dict[str, Any], synth_text: str,
    web_sources: list[dict[str, str]], source_art_id: str | None,
) -> dict[str, Any]:
    parsed = _parse_synth(synth_text, [s for s in pb["steps"][-1].get("sections", [])])
    sections = parsed.get("sections") or {}
    highlights = _clean_highlights(parsed.get("highlights"))

    # Snapshot every prior step's output into the artifact so it survives
    # independently of the playbook_run file. The synth step itself is skipped
    # (it IS the artifact body) — we only embed the upstream research/analysis
    # transcripts. Stored on provenance for UI rendering and appended to
    # raw_markdown so the .md export is self-contained.
    step_outputs: list[dict[str, Any]] = []
    for s in run.get("steps", []):
        if s.get("type") == "synthesize":
            continue
        if s.get("status") != "complete":
            continue
        out = (s.get("output") or "").strip()
        if not out:
            continue
        step_outputs.append({
            "id": s.get("id"),
            "label": s.get("label", ""),
            "type": s.get("type"),
            "output": out,
            "tokens": s.get("tokens") or {},
            "web_sources": s.get("web_sources") or [],
        })

    raw_md = _render_artifact_markdown(parsed, web_sources, highlights, step_outputs)
    art = artifacts.create_artifact(
        ws,
        artifact_type=pb["artifact_type"],
        title=parsed.get("title") or pb["label"],
        tldr=parsed.get("tldr") or "",
        sections={k: v for k, v in sections.items() if isinstance(v, str)},
        highlights=highlights,
        raw_markdown=raw_md,
        provenance={
            "playbook_id": pb["id"],
            "playbook_run_id": run["id"],
            "scenario": run["user_inputs"]["scenario"],
            "source_artifact_ids": [source_art_id] if source_art_id else [],
            "web_sources": web_sources,
            "step_outputs": step_outputs,
        },
    )
    return art


def _render_artifact_markdown(
    parsed: dict[str, Any],
    web_sources: list[dict[str, str]],
    highlights: list[dict[str, str]] | None = None,
    step_outputs: list[dict[str, Any]] | None = None,
) -> str:
    title = parsed.get("title") or "Brief"
    tldr = parsed.get("tldr") or ""
    sections = parsed.get("sections") or {}
    lines = [f"# {title}", "", f"**TL;DR:** {tldr}", ""]
    if highlights:
        lines.append("## Highlights")
        for h in highlights:
            tone = h.get("tone", "claim").upper()
            lines.append(f"- **[{tone}]** {h.get('text', '')}")
        lines.append("")
    for k, v in sections.items():
        if not isinstance(v, str):
            continue
        lines.append(f"## {k}")
        lines.append(v.strip() or "_(empty)_")
        lines.append("")
    if web_sources:
        lines.append("## Sources")
        for s in web_sources:
            t = s.get("title") or s.get("url", "")
            u = s.get("url", "")
            lines.append(f"- [{t}]({u})")
        lines.append("")
    # Per-step research transcript — appended last so the brief reads top-down
    # but the underlying analysis is preserved for audit. Each step is gated by
    # a single H2 so markdown viewers can table-of-contents it.
    if step_outputs:
        lines.append("---")
        lines.append("")
        lines.append("# Playbook research transcript")
        lines.append("")
        lines.append("_Every prior step's full output — captured so the brief is self-contained._")
        lines.append("")
        for s in step_outputs:
            lines.append(f"## {s.get('label') or s.get('id', 'Step')}")
            tok = s.get("tokens") or {}
            in_t = tok.get("input", 0)
            out_t = tok.get("output", 0)
            if in_t or out_t:
                lines.append(f"_{s.get('type', 'step')} · {in_t:,} in · {out_t:,} out_")
                lines.append("")
            lines.append((s.get("output") or "").strip())
            lines.append("")
    return "\n".join(lines)


def refine_artifact(
    ws: Workspace, art_id: str,
    *, include_qa: bool = False, include_conversation: bool = False,
) -> dict[str, Any]:
    """Apply open reviewer comments to an artifact via a single LLM call.

    Pulls the artifact + every comment with status == "open", asks the model to
    revise just the affected sections (keeping the section schema intact), then
    snapshots the result as a new version. Comments that were handled are
    marked `addressed` so reviewers can verify before flipping to `resolved`.

    Optional context sources:
      - `include_qa`: fold the artifact's qa_history into the prompt as reviewer
        clarifications. Useful for ConversationNote / Foresight briefs the user
        has been probing via follow-up Q&A.
      - `include_conversation`: when the artifact's provenance carries a
        `conversation_id` (set on ConversationNote artifacts), inject the last
        few turns of that conversation as context.
    """
    art = artifacts.get_artifact(ws, art_id)
    if not art:
        raise ValueError("artifact not found")

    open_comments = [c for c in art.get("comments", []) if c.get("status") == "open"]
    qa_history = art.get("qa_history") or [] if include_qa else []
    conversation_block = ""
    if include_conversation:
        conv_id = (art.get("provenance") or {}).get("conversation_id")
        if conv_id:
            conv = conv_store.get_conversation(ws, conv_id)
            if conv:
                conversation_block = conv_store.conversation_history_text(conv, max_turns=8)

    if not open_comments and not qa_history and not conversation_block:
        raise ValueError("Nothing to apply — add a comment, include Q&A, or include the source conversation")

    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured")

    section_keys = list((art.get("sections") or {}).keys())
    if not section_keys:
        raise ValueError("Artifact has no sections to revise")
    sections_clause = "\n".join(f'  - "{s}"' for s in section_keys)

    # Group comments by section so the model sees coherent feedback per area.
    grouped: dict[str, list[dict[str, Any]]] = {"_document": []}
    for c in open_comments:
        key = c.get("section") or "_document"
        grouped.setdefault(key, []).append(c)

    feedback_lines: list[str] = []
    for section, comments in grouped.items():
        header = "Document-level feedback" if section == "_document" else f'Section: "{section}"'
        feedback_lines.append(f"### {header}")
        for c in comments:
            who = c.get("author") or "Reviewer"
            feedback_lines.append(f"- [{who}] {c.get('text', '').strip()}")
        feedback_lines.append("")
    feedback_block = "\n".join(feedback_lines).strip() or "(no open reviewer comments)"

    # Optional context blocks.
    qa_block = ""
    if qa_history:
        qa_lines: list[str] = []
        for entry in qa_history[-6:]:  # cap at 6 most recent Q&A pairs
            q = (entry.get("question") or "").strip()
            a = (entry.get("answer") or "").strip()
            if not q:
                continue
            qa_lines.append(f"Q: {q}\nA: {a}")
        if qa_lines:
            qa_block = "\n\n".join(qa_lines)

    current = {
        "title": art.get("title", ""),
        "tldr": art.get("tldr", ""),
        "sections": art.get("sections", {}),
    }

    system = (
        "You revise an executive brief in response to reviewer comments.\n\n"
        "House style — CLEAR, CONCISE, COHERENT, COMPLETE:\n"
        "- Concrete, opinionated, brief. No preamble, no filler.\n"
        "- TL;DR ≤ 30 words.\n"
        "- Section bodies: 3–5 bullets OR ≤ 4 short sentences. Never both.\n"
        "- One idea per bullet, ≤ 22 words.\n"
        "PRESERVE — never drop load-bearing detail from the current brief:\n"
        "- Every dollar amount, percentage, ratio, date, deadline already present.\n"
        "- Every named entity (company, product, person, regulation, file).\n"
        "- Every source_file citation and web attribution.\n"
        "- Every kill gate, verdict, recommendation.\n"
        "If brevity conflicts with a load-bearing fact, KEEP THE FACT and trim prose instead.\n"
        "Untouched sections must stay byte-identical.\n\n"
        "Output STRICT JSON with no preamble and no code fences. Shape:\n"
        '{\n'
        '  "title": "<keep or refine the title>",\n'
        '  "tldr": "≤ 30 words — the single most important takeaway",\n'
        '  "sections": {\n'
        '    "<section_name>": "<3–5 bullets OR ≤ 4 short sentences>",\n'
        '    ...\n'
        '  },\n'
        '  "changelog": "one short line describing what changed"\n'
        '}\n\n'
        f"The `sections` keys MUST be exactly these (in this order):\n{sections_clause}"
    )

    extra_context_parts: list[str] = []
    if qa_block:
        extra_context_parts.append(
            "Follow-up Q&A on this brief (treat as reviewer clarifications — "
            "fold any new facts or corrections they raise into the right section):\n"
            + qa_block
        )
    if conversation_block:
        extra_context_parts.append(
            "Source conversation transcript (the brief was derived from this "
            "conversation — fold in anything the brief is missing):\n"
            + conversation_block
        )
    extras = ("\n\n" + "\n\n".join(extra_context_parts)) if extra_context_parts else ""

    user = (
        f"Current brief (JSON):\n{json.dumps(current, indent=2)}\n\n"
        f"Reviewer comments to address:\n{feedback_block}{extras}\n\n"
        "Return the revised brief as JSON now.\n\n"
        "Section handling:\n"
        "- Rewrite every section that has a direct comment, plus the TL;DR.\n"
        "- For un-commented sections: inspect whether your revisions create an "
        "  inconsistency (a claim, number, framing, or recommendation that no "
        "  longer matches the rest of the brief). If yes, propagate the minimum "
        "  change needed to restore coherence — do not rewrite for style. If no, "
        "  keep the section byte-identical.\n"
        "- In the `changelog` field, list (a) sections rewritten because of a "
        "  direct comment, and (b) sections touched only to maintain consistency. "
        "  Format: \"Direct: A, B. Propagated: C (kept aligned with A).\""
    )

    # Reuse the model the originating run picked so refinement matches the
    # original style. Fall back to env default for legacy artifacts.
    origin_run_id = (art.get("provenance") or {}).get("playbook_run_id")
    origin_model = None
    if origin_run_id:
        origin_run = get_run(ws, origin_run_id)
        if origin_run:
            origin_model = origin_run.get("user_inputs", {}).get("answer_model")
    model = _resolve_model(origin_model)
    raw, _tokens, _web = _synth_llm_call(client, model, system, user, web_grounding=False)

    parsed = _parse_synth(raw, section_keys)
    new_sections = {k: v for k, v in (parsed.get("sections") or {}).items() if isinstance(v, str)}
    new_tldr = parsed.get("tldr") or art.get("tldr", "")
    changelog = (parsed.get("changelog") or "").strip()

    # Preserve web_sources from the most recent version's markdown so the rendered
    # output keeps citations from the original synth.
    web_sources = (art.get("provenance") or {}).get("web_sources") or []
    new_raw_md = _render_artifact_markdown(
        {"title": parsed.get("title") or art.get("title", ""), "tldr": new_tldr, "sections": new_sections},
        web_sources,
    )

    updated = artifacts.add_version(
        ws,
        art_id,
        tldr=new_tldr,
        sections=new_sections,
        raw_markdown=new_raw_md,
        summary=changelog or f"Refined from {len(open_comments)} comment(s)",
        addressed_comment_ids=[c["id"] for c in open_comments],
    )
    return updated  # type: ignore[return-value]


def suggest_patch_from_parent(
    ws: Workspace, child_id: str, parent_id: str,
    *, from_version: int | None = None, to_version: int | None = None,
) -> dict[str, Any]:
    """Diff-aware suggestion: given a change in a parent artifact, propose
    targeted patches for a downstream (child) artifact whose provenance lists
    the parent. Does NOT mutate the child.

    Returns: { summary, parent_changes: [{section, before, after}], suggested_changes:
    [{section, rationale, proposed_text}], instruction }
    The `instruction` field is a paste-ready prompt the user can hand to
    refine_artifact (or our /api/artifacts/{id}/refine endpoint).
    """
    child = artifacts.get_artifact(ws, child_id)
    if not child:
        raise ValueError("child artifact not found")
    parent = artifacts.get_artifact(ws, parent_id)
    if not parent:
        raise ValueError("parent artifact not found")
    if parent_id not in ((child.get("provenance") or {}).get("source_artifact_ids") or []):
        raise ValueError("parent is not a source artifact of the child")

    versions = parent.get("versions") or []
    if len(versions) < 2:
        raise ValueError("parent has only one version — nothing to diff")

    # Default: diff the two most recent versions.
    to_v = to_version if to_version is not None else versions[-1]["v"]
    from_v = from_version if from_version is not None else versions[-2]["v"]
    v_to = next((v for v in versions if v["v"] == to_v), None)
    v_from = next((v for v in versions if v["v"] == from_v), None)
    if not v_to or not v_from:
        raise ValueError("requested version not found")
    if v_from["v"] >= v_to["v"]:
        raise ValueError("from_version must be older than to_version")

    client = _anthropic_client()
    if not client:
        raise RuntimeError("LLM not configured")

    # Build the parent-side change summary deterministically — only sections
    # whose text actually differs. Keeps the LLM input bounded.
    parent_changes: list[dict[str, str]] = []
    before_sections = v_from.get("sections") or {}
    after_sections = v_to.get("sections") or {}
    if (v_from.get("tldr") or "") != (v_to.get("tldr") or ""):
        parent_changes.append({
            "section": "TL;DR",
            "before": v_from.get("tldr") or "",
            "after": v_to.get("tldr") or "",
        })
    for k in list(set(list(before_sections.keys()) + list(after_sections.keys()))):
        if (before_sections.get(k) or "").strip() != (after_sections.get(k) or "").strip():
            parent_changes.append({
                "section": k,
                "before": (before_sections.get(k) or "").strip(),
                "after": (after_sections.get(k) or "").strip(),
            })
    if not parent_changes:
        raise ValueError("no textual diff between the chosen parent versions")

    child_sections = child.get("sections") or {}
    section_keys = list(child_sections.keys())
    sections_clause = "\n".join(f'  - "{s}"' for s in section_keys) if section_keys else "  (child has no structured sections)"

    parent_diff_lines: list[str] = []
    for ch in parent_changes:
        parent_diff_lines.append(f"### Section: {ch['section']}")
        parent_diff_lines.append(f"BEFORE (v{from_v}):\n{ch['before']}\n")
        parent_diff_lines.append(f"AFTER (v{to_v}):\n{ch['after']}\n")
    parent_diff_block = "\n".join(parent_diff_lines)

    child_block = json.dumps({
        "title": child.get("title", ""),
        "tldr": child.get("tldr", ""),
        "sections": child_sections,
    }, indent=2)

    system = (
        "You are reviewing a downstream artifact whose upstream source has been revised. "
        "Your job is to propose *minimum-scope* targeted patches to the downstream artifact "
        "so it stays coherent with the new upstream — nothing more.\n\n"
        "Rules:\n"
        "- Only touch sections that are genuinely affected by the upstream change. Leave "
        "  the rest byte-identical.\n"
        "- Do NOT rewrite for style. Do NOT add new ideas. Do NOT remove well-supported "
        "  downstream claims that the upstream change doesn't contradict.\n"
        "- If the downstream artifact has no section that's affected, return an empty "
        "  suggested_changes list and say so in `summary`.\n\n"
        "Output STRICT JSON only — no preamble, no code fences. Shape:\n"
        '{\n'
        '  "summary": "<1-2 sentences describing what cascade is needed, or why none>",\n'
        '  "suggested_changes": [\n'
        '    {\n'
        '      "section": "<name from the downstream sections list, or \\"TL;DR\\">",\n'
        '      "rationale": "<one short sentence — which upstream change forces this>",\n'
        '      "proposed_text": "<the full new text for this section>"\n'
        '    },\n'
        '    ...\n'
        '  ]\n'
        '}\n\n'
        f"The downstream artifact's section names are:\n{sections_clause}"
    )
    user = (
        f"Upstream change (parent v{from_v} → v{to_v}):\n{parent_diff_block}\n\n"
        f"Downstream artifact (current state):\n{child_block}\n\n"
        "Propose the targeted patches now."
    )

    raw, _tokens, _web = _synth_llm_call(client, _resolve_model(None), system, user, web_grounding=False)

    # Parse the model's JSON output. If parse fails, return raw text in summary.
    parsed: dict[str, Any]
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except Exception:
        parsed = {"summary": raw, "suggested_changes": []}

    suggested = parsed.get("suggested_changes") or []
    # Build a paste-ready instruction the user can hand to refine_artifact.
    instr_lines = [
        f"The parent artifact '{parent.get('title', '')}' was revised (v{from_v} → v{to_v}).",
        "Apply the following targeted updates to this artifact and nothing else:",
    ]
    for sc in suggested:
        sec = (sc.get("section") or "").strip() or "(unspecified section)"
        rat = (sc.get("rationale") or "").strip()
        prop = (sc.get("proposed_text") or "").strip()
        instr_lines.append(f"- In section \"{sec}\": {rat}")
        if prop:
            instr_lines.append(f"  Proposed text:\n  {prop}")
    instruction = "\n".join(instr_lines)

    return {
        "summary": parsed.get("summary", ""),
        "parent_id": parent_id,
        "from_version": from_v,
        "to_version": to_v,
        "parent_changes": parent_changes,
        "suggested_changes": suggested,
        "instruction": instruction,
    }


def _strip_code_fence(text: str) -> str:
    """Tolerate model output wrapped in ```json ... ``` despite the system
    prompt forbidding it."""
    t = text.strip()
    if t.startswith("```"):
        # Drop the opening fence (e.g. ```json) and the trailing fence.
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def _template_step(s: dict[str, Any]) -> dict[str, Any]:
    """Public step shape for /api/playbooks — surfaces `intent` for intent_turn
    steps so the kickoff preview can show which library intent each step uses."""
    out: dict[str, Any] = {"id": s["id"], "label": s["label"], "type": s["type"]}
    if s.get("type") == "intent_turn" and s.get("intent"):
        out["intent"] = s["intent"]
    return out


def list_playbooks(ws: Workspace | None = None) -> list[dict[str, Any]]:
    """Return template metadata for the frontend. When ws is given, user-defined
    playbooks (workspace + global) are merged in with a `source` field. A user
    record sharing an id with a built-in is treated as an override and surfaces
    once with source='customized'.
    """
    user_by_id: dict[str, dict[str, Any]] = {}
    if ws is not None:
        try:
            import playbook_store
            for p in playbook_store.list_playbooks(ws):
                user_by_id[p["id"]] = p
        except Exception:
            pass
    out: list[dict[str, Any]] = []
    for p in PLAYBOOKS.values():
        override = user_by_id.pop(p["id"], None)
        view = override or p
        out.append({
            "id": view["id"],
            "label": view["label"],
            "tagline": view.get("tagline", ""),
            "expected_duration_s": view.get("expected_duration_s", 240),
            "accepts_source_types": view.get("accepts_source_types", []),
            "artifact_type": view.get("artifact_type"),
            "steps": [_template_step(s) for s in view["steps"]],
            "source": "customized" if override else "builtin",
        })
    # Anything left in user_by_id is a non-overlap user playbook.
    if ws is not None:
        try:
            for p in user_by_id.values():
                out.append({
                    "id": p["id"],
                    "label": p["label"],
                    "tagline": p["tagline"],
                    "expected_duration_s": p["expected_duration_s"],
                    "accepts_source_types": p["accepts_source_types"],
                    "artifact_type": p["artifact_type"],
                    "steps": [_template_step(s) for s in p["steps"]],
                    "source": p.get("scope", "workspace"),
                })
        except Exception:
            pass
    return out


def horizon_options() -> dict[str, str]:
    """The horizons the frontend should expose in the kickoff form."""
    return dict(HORIZONS)


# ---------- Scenario suggestions for the kickoff form ----------------------
# Mix two flavors of suggestion so the user isn't trapped inside their corpus:
#
#  - "kb"       : 2 suggestions that name SPECIFIC entities from the graph
#                 (communities + god nodes). Real, grounded, anchored.
#  - "wildcard" : 1 suggestion that the corpus is silent on but fits the
#                 company's positioning (derived from the workspace's default
#                 rubric — the canonical company brief). Lets the LLM stretch
#                 the user beyond what they've already ingested.
#
# Each call hits the LLM — no caching — so users get fresh suggestions every
# time the kickoff form opens or they tap Refresh.


def _company_context(ws: Workspace) -> str:
    """Best-effort one-paragraph company context for the wildcard prompt.

    Prefer the workspace's first rubric body (the canonical "company brief" —
    capital constraints, distribution leverage, positioning, etc.). Fall back
    to the workspace name only when no rubric is configured."""
    try:
        rs = rubric_store.list_rubrics(ws)
        if rs:
            body = (rs[0].get("body") or "").strip()
            if body:
                # Cap to keep token cost predictable — first ~1.5KB is enough.
                return body[:1500]
    except Exception:
        pass
    return f"Workspace: {ws.name or ws.id}"


def _suggest_prompt(playbook: dict[str, Any], insights: dict[str, Any], company_context: str) -> str:
    """Build the LLM prompt for scenario suggestions.

    Two-source structure so the LLM produces 2 KB-grounded + 1 wildcard. The
    KB chips name real corpus entities; the wildcard explicitly steps outside
    the corpus but stays inside the company's lane."""
    community_labels = list((insights.get("community_labels") or {}).values())
    gods = [g.get("label") if isinstance(g, dict) else g for g in (insights.get("gods") or [])]
    gods = [g for g in gods if g]
    return (
        f"You're suggesting concrete scenarios for the '{playbook['label']}' playbook.\n"
        f"Playbook purpose: {playbook['tagline']}\n\n"
        "=== COMPANY CONTEXT (use for the wildcard) ===\n"
        f"{company_context}\n\n"
        "=== KNOWLEDGE BASE THEMES (communities — use for the KB scenarios) ===\n"
        + "\n".join(f"- {c}" for c in community_labels[:14])
        + "\n\n=== LOAD-BEARING KB ENTITIES (use for the KB scenarios) ===\n"
        + "\n".join(f"- {g}" for g in gods[:14])
        + "\n\nProduce a JSON object with exactly this shape:\n"
        '{\n'
        '  "kb": [\n'
        '    "Scenario 1 that names a SPECIFIC entity/theme from the KB above.",\n'
        '    "Scenario 2 that names a DIFFERENT specific entity/theme."\n'
        '  ],\n'
        '  "wildcard": "Scenario the KB is SILENT on but that fits the company context above."\n'
        '}\n\n'
        "Rules:\n"
        "- KB scenarios: each must reference at least one named entity, product, or theme from "
        "the lists above. No generic templates. Across calls, pick DIFFERENT entities/themes — "
        "don't repeat the same anchors. Surprise the user with angles they wouldn't immediately "
        "think of.\n"
        "- Wildcard: must be a scenario the KB does NOT cover. It should be plausible and "
        "actionable for THIS company given the company context (use the rubric body), not a "
        "random idea from the wider tech industry. Think 'adjacent move the team hasn't loaded "
        "into the KB yet'. Vary the angle each call — different industry, different buyer, "
        "different motion.\n"
        "- Length: 1-2 sentences each. Phrase as a scenario the user brings in, not as the "
        "playbook's job description.\n"
        "- Return ONLY the JSON. No commentary, no markdown fences.\n"
    )


# Per-(workspace, playbook) scenario-suggestion cache. Cached for 15 minutes so
# repeatedly re-opening the kickoff form doesn't burn an LLM call each time.
# The "↻ Refresh" button passes fresh=True to bypass the cache and get a new
# set — that's the explicit "vary the angle" lever the original design exposed.
_SUGGEST_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
_SUGGEST_CACHE_TTL_S = 15 * 60


def suggest_scenarios(ws: Workspace, playbook_id: str, fresh: bool = False) -> list[dict[str, str]]:
    """Return up to 3 scenario suggestions (2 kb + 1 wildcard) for the
    kickoff form. Each suggestion is `{text, kind}` where kind is 'kb' or
    'wildcard'. Empty list when there's no API key, no graph, or the LLM
    call fails — the UI falls back to a static placeholder.

    Cached for 15 minutes per (workspace, playbook). Pass `fresh=True` to
    bypass and re-roll — wired to the kickoff form's "↻ Refresh" button so
    users can still get new angles on demand."""
    pb = PLAYBOOKS.get(playbook_id)
    if not pb:
        return []

    cache_key = (ws.id, playbook_id)
    if not fresh:
        cached = _SUGGEST_CACHE.get(cache_key)
        if cached is not None:
            cached_at, cached_suggestions = cached
            if cached_suggestions and (time.time() - cached_at) < _SUGGEST_CACHE_TTL_S:
                return cached_suggestions

    # Need an insights file with at least communities — otherwise the KB
    # suggestions would be confabulation.
    if not ws.insights_file.exists():
        return []
    try:
        insights = json.loads(ws.insights_file.read_text())
    except Exception:
        return []
    if not insights.get("community_labels") and not insights.get("gods"):
        return []

    client = _anthropic_client()
    if not client:
        return []

    prompt = _suggest_prompt(pb, insights, _company_context(ws))
    try:
        msg = client.messages.create(
            model=os.environ.get("GRAPHIFY_SUGGEST_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=700,
            # High temperature so repeat calls don't churn out near-identical
            # suggestions. The KB anchor keeps them grounded; temperature
            # only shifts which angle the model takes.
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.MULTILINE)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            return []
        kb_items = [str(s).strip() for s in (parsed.get("kb") or []) if str(s).strip()][:2]
        wildcard = str(parsed.get("wildcard") or "").strip()
        suggestions: list[dict[str, str]] = [{"text": s, "kind": "kb"} for s in kb_items]
        if wildcard:
            suggestions.append({"text": wildcard, "kind": "wildcard"})
    except Exception:
        return []

    if suggestions:
        _SUGGEST_CACHE[cache_key] = (time.time(), suggestions)
    return suggestions
