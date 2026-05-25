# Company Brain — Use Cases Per Phase

Version 1.0 · 2026-05-25

Companion to `COMPANY_BRAIN_ROADMAP.md`. Same phases, but explained as plain stories. The point of this document is to read each phase and immediately understand: **who is this for, what do they want to do, and what does this phase let them do that they could not do before?**

To keep it concrete, the same three people appear throughout:

- **Riya** — Support team lead. Knows refund policy cold. Cannot write code.
- **Dev** — On-call engineer. Knows the runbook in his head and a few Slack threads.
- **Sam** — Head of operations. Decides what the company will trust automation to do.

---

## Phase 0 — Skill schema and registry

### What this phase is really about

Right now, "how we issue a refund" is a vague paragraph in a handbook. Nothing lists it. Nothing knows its inputs. Nothing can call it.

After this phase, every workflow the company knows about shows up as a **row in a list** — with a name, what it takes in, what it returns, who owns it, and a version number. The same way you see a list of files in a folder.

### Use case 1 — "What does my company actually know how to do?"

Sam opens the app and clicks the **Skills** tab. He sees a clean list:

- Issue refund under $50 — owned by Riya — v1
- Triage incoming product idea — owned by the PM team — v3
- Escalate VIP customer complaint — owned by Riya — v1

He clicks one. He sees what it takes (an order ID, a reason) and what it returns (a refund confirmation or a rejection). Nothing runs yet. But for the first time, the company's know-how is visible in one place.

### Use case 2 — "Can an outside system find this?"

An AI agent built by another team asks the brain: "Do you have any skills for handling refunds?" The brain answers with a real list, not a chatty paragraph. The agent now knows what is available, even if it does nothing with it yet.

### Why this has to come first

Everything later in the roadmap — running, approving, testing, mining — assumes there is a thing called a "skill" with a known shape. Phase 0 defines that shape.

---

## Phase 1 — Tool execution and dry-run

### What this phase is really about

A skill that only describes itself is just a document. This phase lets a skill **actually do something** — call a tool, get a result back, record what happened. And it does this in a fake mode first, so nothing real breaks.

### Use case — "Show me this skill working, but don't touch anything real yet"

Riya opens the `Issue refund under $50` skill. She clicks **Run (dry-run)** and types in a test order ID.

She watches:

1. The skill receives the order ID.
2. It checks the refund amount is under $50. It is.
3. It calls the refund tool. (Behind the scenes, this is a fake refund tool that always says "ok.")
4. It returns "Refund confirmed for order 12345."

No real money moved. No real customer was emailed. But Riya has just seen her policy execute, step by step, with a real result. She shows it to Sam. Sam can see what this skill would do **before** anyone connects it to the real Stripe.

### Why this matters

Until a workflow can be run and watched, it is just words. This phase turns words into something you can demo. The dry-run mode is the safety net that lets the team build confidence before any real action.

---

## Phase 2 — Safety: approvals and audit

### What this phase is really about

Some skills are safe to run automatically. Most are not. This phase puts a human in the loop where it matters, and writes down every single thing that happens so you can prove it later.

### Use case 1 — "I want a human to sign off before real money moves"

Riya marks `Issue refund over $500` as **approval-required**. She also flips its mode to live (real refund API, not the fake one). Now whenever an AI agent — or anyone — invokes this skill, it does **not** run straight through.

Instead, it pauses. A card appears in the **Approvals** tab:

> Pending: Issue refund over $500 for order 87654, amount $612, requested by support-agent-bot.

Sam reads it. He can see the inputs, the trace so far, the reason. He clicks **Approve**. The skill resumes and completes. If he had clicked Reject, nothing would have happened and the requester would get a clean "rejected by Sam."

### Use case 2 — "Prove to me, three months later, exactly what happened"

A customer disputes a refund. Riya opens the audit log, searches by order number, and sees:

- Skill: Issue refund over $500
- Invoked by: support-agent-bot at 14:02
- Inputs: order 87654, amount $612, reason "shipping damage"
- Approved by: Sam at 14:04
- Tool calls: refund_api → success at 14:04:09
- Final output: "Refund confirmed."

Every line is timestamped, every approver named, every tool call recorded. The company can answer the question "who decided to do this and why?" without anyone having to remember.

### Why this matters

No company will let AI touch real systems without these two things: a human gate on risky actions, and a complete record of everything that happened.

---

## Phase 3 — Capturing what lives in people's heads

### What this phase is really about

The biggest blocker isn't the documents you have. It's the knowledge you **don't** have written down — the stuff that lives in Riya's head and Dev's Slack history.

This phase gives you two ways to pull that out: a guided conversation, and a paste-a-transcript shortcut.

### Use case 1 — "Riya, can you spend 10 minutes telling the brain how refunds really work?"

Riya clicks **Start interview** on the Skills tab and types "Issuing refunds." A conversation opens. The brain asks her short, sharp questions, one at a time:

- "What kicks off a refund? Is it always a customer asking, or do we issue them on our own?"
- "What's the first thing you check?"
- "When do you say no?"
- "What's the cap before you need approval?"
- "If the customer is on the enterprise plan, does anything change?"

As Riya answers, a skill draft builds up on the right side of the screen — trigger, preconditions, action steps, escalation. Ten minutes later, she has a real, structured skill draft. She edits a couple of words and promotes it. The company's first real piece of tacit knowledge is now captured.

### Use case 2 — "Dev, paste that Slack thread from the outage"

Dev had a tough night last week — a disk filled up, took 40 minutes to fix, and the only record is a long Slack thread. He pastes the whole thread into the **Trace-to-skill** box and clicks Propose.

The brain reads the thread and proposes a draft skill: `Handle database disk-full alert`. It has:

- Trigger: PagerDuty alert "disk > 90%"
- First check: which volume, which service
- Action: identify largest tables, archive or rotate logs
- Escalation: if disk > 98%, page the DBA

Dev tweaks a couple of steps, adds a missing check, and saves. The runbook that lived in his head and a Slack thread is now a skill the team can find next time.

### Why this matters

This is the single most important capability in the whole roadmap. Every other Company Brain attempt has been a wiki + chatbot. The interview mode and trace shortcut are how you actually get knowledge **out of people** and into the system — without making them write documentation.

---

## Phase 4 — Finding skills the company didn't know it had

### What this phase is really about

Even after Riya and Dev contribute interviews, there are workflows hiding in existing docs that no one thought to capture. This phase mines the existing knowledge graph for those workflows and **proposes** them.

### Use case — "What workflows do we have that I haven't even thought to write down?"

Sam opens the **Discovery** sub-tab. He sees a list the brain has surfaced from existing support docs and PDFs:

- Probable skill: Handle chargeback dispute — 4 citations across 2 docs
- Probable skill: Process VIP refund escalation — 6 citations, mostly in policy doc
- Probable skill: Send order-delay apology — 3 citations in support templates

Each one has a short summary and a "Start interview" button. Sam picks the chargeback one and assigns it to Riya. Riya runs a quick interview to confirm and fill the gaps. Within an hour, the team has discovered and captured a workflow nobody had written down.

### Why this matters

Interviews capture what people **think to mention**. Mining captures what's **already there** but invisible. Together they cover what one alone misses.

---

## Phase 5 — Trust: testing, confidence, citations

### What this phase is really about

A skill exists. But how does anyone know it actually works? And when an outside AI calls it, how does that AI decide whether to trust it?

This phase adds three things: real tests, a confidence number, and a trail back to the source for every step.

### Use case 1 — "Prove this skill works before I trust it"

Riya adds three real past refund tickets as test cases — including the awkward edge case where a customer asked for a refund on a partially-shipped order.

She clicks **Run tests**. The brain runs all three in dry-run and reports:

- Test 1 (simple refund): PASS
- Test 2 (over the cap, should escalate): PASS
- Test 3 (partial shipment): FAIL — the skill refunded the full amount, but the correct behavior was to refund the shipped half.

Riya fixes the step that handles partial shipments, re-runs. 3/3 pass. The skill now shows **97% confidence** as a badge.

### Use case 2 — "When the AI is unsure, ask a human"

A skill called `Approve marketing copy` has only 60% confidence — it was just authored, not many tests yet. An external AI agent invokes it. Instead of running, the brain returns: **"Confidence below threshold (60% < 80%) — needs human review."** The AI escalates to a human. Nothing risky happens.

### Use case 3 — "Where did this step come from?"

Sam clicks into a skill and hovers over a step that says "Escalate if amount > $500." The brain shows: **"From Riya's interview on May 25, turn 7."** Every step has this. If a skill ever does the wrong thing, you can trace it to the exact human input that put it there.

### Why this matters

A company will not trust an automated workflow without these signals. Tests prove correctness. Confidence tells callers how much to lean on the skill. Citations make every step accountable.

---

## Phase 6 — External agents using the brain

### What this phase is really about

This is the payoff. Everything in phases 0 through 5 exists so that an AI agent — running anywhere, written by anyone — can discover the company's skills and use them safely.

### Use case — "A support AI in another product uses our refund skill"

A customer messages a support AI agent built by a different team, in a different system: "I want a refund on order 12345."

In about three seconds, behind the scenes:

1. The agent asks the brain: "What skills do you have?"
2. It finds `issue_refund_under_50` and reads its inputs schema.
3. It invokes the skill with the order ID and reason.
4. The skill is marked approval-tier for amounts near the cap. It pauses.
5. A card appears in Sam's Approvals tab. He approves.
6. The skill calls the (mock) refund tool. Confirms. Returns to the agent.
7. The agent tells the customer "Your refund is confirmed."

The whole loop is visible in the app: the external call, the trace, the approval, the citation back to Riya's original interview. Sam can show this to any pilot customer and say: "This is what your company brain does."

### Why this matters

This is the single demoable proof of the entire thesis. Tacit knowledge from Riya → typed skill → invoked by an AI built elsewhere → safely executed → fully audited. Eight weeks of work, one ten-minute story.

---

## How to read this document together with the roadmap

- `COMPANY_BRAIN_ROADMAP.md` answers: **what are we building, in what order, with what acceptance bar?**
- `COMPANY_BRAIN_USECASES.md` (this file) answers: **why does each phase exist, in plain language, for someone non-technical?**

Use this one to onboard a new teammate, brief a stakeholder, or pressure-test whether each phase actually earns its place in the plan. If a phase here doesn't tell a clear story, the phase itself probably needs to be cut or merged.
