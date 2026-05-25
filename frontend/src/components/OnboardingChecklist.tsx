import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";

// In-app onboarding. Walks the user from "no workspace" to their first
// Playbook artifact (the "aha" moment) and on to their first KB refinement
// (the compound moment).
//
// Rubric + Memory are now per-workspace. The create-workspace form offers a
// rubric snapshot picker so a new workspace can inherit a sibling's framing.
// We surface them as one info note after the aha so the user knows where to
// deepen the brain later.

type StepId = "workspace" | "docs" | "rubric" | "playbook" | "refine";

type Step = {
  id: StepId;
  title: string;
  body: string;
  cta?: string;
  ctaTab?: "playbooks" | "refine" | "conversations" | "guide";
  isCreateWorkspace?: boolean;
  isOpenRubrics?: boolean;
};

const STEPS: Step[] = [
  {
    id: "workspace",
    title: "Create a workspace",
    body: "A workspace is your team's strategy brain. Click below — give it a name like \"Marketplace 2026\" or whatever scope this brain should hold.",
    cta: "Create workspace",
    isCreateWorkspace: true,
  },
  {
    id: "docs",
    title: "Add your first document",
    body: "Drop in 2-5 of your most load-bearing docs — PDFs, decks, board memos. The graph builds in ~60s per PDF.",
  },
  {
    id: "rubric",
    title: "View and refine your Rubric",
    body: "Your Rubric is the framing rules applied to every LLM answer and Playbook. Open it, read the built-in Appfire Context, and edit it (or add your own) so the brain reasons the way your team does.",
    cta: "Open Rubric",
    isOpenRubrics: true,
  },
  {
    id: "playbook",
    title: "Run your first Playbook",
    body: "Try \"Find unexplored ideas\". Three real scenarios are already drawn from your KB. Click one, hit Run, and you'll have a typed brief in ~5 minutes. Your Rubric + Memory are auto-applied.",
    cta: "Open Playbooks",
    ctaTab: "playbooks",
  },
  {
    id: "refine",
    title: "Refine your first fact",
    body: "Spot something the KB got wrong? Open Refine KB, edit it in place. The LLM cites your version with your name from then on.",
    cta: "Open Refine KB",
    ctaTab: "refine",
  },
];

const LS_DISMISS_KEY = "innobrain.onboarding.dismissed";

type Props = {
  workspaceId: string | null;
  filesCount: number;
  /** Bumped whenever the rubrics drawer opens (no longer a step, but kept
   *  for future-proofing — currently unused by the new flow). */
  rubricsOpenedTick?: number;
  onNavigateTab: (tab: "playbooks" | "refine" | "conversations" | "guide") => void;
  onOpenRubrics: () => void;
  /** Triggers the workspace switcher's "Create workspace" form. */
  onCreateWorkspace: () => void;
  /** Bumped by the header's "Quickstart" button to forcibly re-show the
   *  checklist for the current workspace after the user has dismissed it. */
  reopenTick?: number;
};

export function OnboardingChecklist({
  workspaceId,
  filesCount,
  rubricsOpenedTick,
  onNavigateTab,
  onOpenRubrics,
  onCreateWorkspace,
  reopenTick,
}: Props) {
  const wsKey = workspaceId ?? "none";

  // Persisted: has the user dismissed this for the current workspace?
  // Dismissal is per-workspace so creating a fresh workspace re-arms the
  // onboarding (intentional — the new brain deserves the same setup walk).
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(`${LS_DISMISS_KEY}.${wsKey}`) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(`${LS_DISMISS_KEY}.${wsKey}`) === "1");
    } catch {
      setDismissed(false);
    }
  }, [wsKey]);

  // External "reopen" signal — header's Quickstart button bumps this to
  // forcibly re-show the checklist for the current workspace after dismissal.
  useEffect(() => {
    if (reopenTick === undefined || reopenTick === 0) return;
    try {
      window.localStorage.removeItem(`${LS_DISMISS_KEY}.${wsKey}`);
    } catch {}
    setDismissed(false);
    setCollapsed(false);
  }, [reopenTick, wsKey]);

  // Polled state for the playbook + refine + rubric steps.
  const [playbookCount, setPlaybookCount] = useState<number | null>(null);
  const [refineCount, setRefineCount] = useState<number | null>(null);
  // Rubric step is "done" once any rubric is customized or user-created OR
  // the user has opened the drawer (viewed it) at least once. Tracked
  // per-workspace now that rubrics are per-workspace.
  const [rubricRefined, setRubricRefined] = useState<boolean | null>(null);
  const [rubricViewed, setRubricViewed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem("innobrain.onboarding.rubricViewed") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (!rubricsOpenedTick) return;
    setRubricViewed(true);
    try {
      window.localStorage.setItem("innobrain.onboarding.rubricViewed", "1");
    } catch {}
  }, [rubricsOpenedTick]);
  const [collapsed, setCollapsed] = useState<boolean>(false);

  const refreshCounts = useCallback(async () => {
    try {
      const [pb, kb, rb] = await Promise.all([
        api.playbookRuns().catch(() => ({ runs: [] })),
        api.kbCorrections().catch(() => ({ corrections: [] })),
        api.rubrics().catch(() => ({ rubrics: [] })),
      ]);
      setPlaybookCount((pb as { runs: unknown[] }).runs.length);
      setRefineCount((kb as { corrections: unknown[] }).corrections.length);
      const rubrics = (rb as { rubrics: { source?: string }[] }).rubrics;
      setRubricRefined(rubrics.some((r) => r.source === "customized" || r.source === "user"));
    } catch {
      /* swallow */
    }
  }, []);

  useEffect(() => {
    if (dismissed) return;
    refreshCounts();
    const t = setInterval(refreshCounts, 30_000);
    return () => clearInterval(t);
  }, [refreshCounts, dismissed, wsKey]);

  const completion: Record<StepId, boolean> = useMemo(
    () => ({
      workspace: !!workspaceId,
      docs: filesCount > 0,
      rubric: !!rubricRefined || rubricViewed,
      playbook: (playbookCount ?? 0) > 0,
      refine: (refineCount ?? 0) > 0,
    }),
    [workspaceId, filesCount, rubricRefined, rubricViewed, playbookCount, refineCount],
  );

  const completedCount = STEPS.filter((s) => completion[s.id]).length;
  const allDone = completedCount === STEPS.length;
  const currentIdx = STEPS.findIndex((s) => !completion[s.id]);
  const currentStepId: StepId | null = currentIdx >= 0 ? STEPS[currentIdx].id : null;

  // Publish current step on <body> so a CSS rule can pulse the relevant UI
  // element. The data attribute clears when dismissed, all done, or counts
  // haven't loaded yet — so the highlight never lingers.
  useEffect(() => {
    const body = document.body;
    if (dismissed || allDone || !currentStepId) {
      delete body.dataset.onboardingStep;
      return;
    }
    body.dataset.onboardingStep = currentStepId;
    return () => { delete body.dataset.onboardingStep; };
  }, [currentStepId, dismissed, allDone]);

  if (dismissed) return null;
  if (playbookCount === null || refineCount === null || rubricRefined === null) return null;

  const handleDismiss = () => {
    try {
      window.localStorage.setItem(`${LS_DISMISS_KEY}.${wsKey}`, "1");
    } catch {}
    setDismissed(true);
  };

  const handleCta = (step: Step) => {
    if (step.isCreateWorkspace) {
      onCreateWorkspace();
      return;
    }
    if (step.isOpenRubrics) {
      onOpenRubrics();
      setTimeout(refreshCounts, 1500);
      return;
    }
    if (step.ctaTab) {
      onNavigateTab(step.ctaTab);
      setTimeout(refreshCounts, 1500);
    }
  };

  return (
    <div className={`onboarding-card ${collapsed ? "onboarding-collapsed" : ""}`}>
      <header className="onboarding-head" onClick={() => setCollapsed((c) => !c)}>
        <div className="onboarding-title">
          <span className="onboarding-icon">{allDone ? "🎉" : "🚀"}</span>
          <span>{allDone ? "You're up and running" : "Quickstart"}</span>
        </div>
        <div className="onboarding-progress">
          <span>{completedCount}/{STEPS.length}</span>
          <button
            className="onboarding-toggle"
            onClick={(e) => { e.stopPropagation(); setCollapsed((c) => !c); }}
            aria-label={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? "▴" : "▾"}
          </button>
          <button
            className="onboarding-close"
            onClick={(e) => { e.stopPropagation(); handleDismiss(); }}
            title="Dismiss for this workspace"
          >
            ×
          </button>
        </div>
      </header>

      {!collapsed && (
        <>
          <div className="onboarding-bar">
            <div
              className="onboarding-bar-fill"
              style={{ width: `${(completedCount / STEPS.length) * 100}%` }}
            />
          </div>

          {allDone ? (
            <div className="onboarding-celebration">
              <p>
                You've taken InnoBrain from no workspace to your team's <em>strategy brain</em>.
                The compound effect starts now — every refinement, every Playbook, every conversation
                makes the next one sharper.
              </p>
              <div className="onboarding-globals">
                <strong>Want to deepen it?</strong> Both scoped to this workspace:
                <ul>
                  <li><button className="onboarding-link" onClick={onOpenRubrics}>📐 Rubrics</button> — this workspace's framing rules, applied to every LLM answer here</li>
                  <li>🧠 Memory — durable facts this workspace remembers across its conversations</li>
                </ul>
              </div>
              <div className="onboarding-actions">
                <a className="onboarding-cta" href="/onboarding.html" target="_blank" rel="noreferrer">
                  📖 Open the full guide ↗
                </a>
                <button className="onboarding-secondary" onClick={handleDismiss}>
                  Hide
                </button>
              </div>
            </div>
          ) : (
            <>
              <ol className="onboarding-steps">
                {STEPS.map((s, i) => {
                  const done = completion[s.id];
                  const isCurrent = i === currentIdx;
                  return (
                    <li
                      key={s.id}
                      className={`onboarding-step ${done ? "done" : ""} ${isCurrent ? "current" : ""}`}
                    >
                      <div className="onboarding-step-marker">{done ? "✓" : isCurrent ? "→" : "○"}</div>
                      <div className="onboarding-step-body">
                        <div className="onboarding-step-title">{s.title}</div>
                        {isCurrent && (
                          <>
                            <div className="onboarding-step-text">{s.body}</div>
                            {s.cta && (
                              <button className="onboarding-cta" onClick={() => handleCta(s)}>
                                {s.cta} →
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
              <div className="onboarding-foot">
                📐 Rubric + 🧠 Memory are <strong>per-workspace</strong>. New workspaces start with a built-in rubric (or one you copy in at creation) and empty memory. Edits stay scoped to this workspace.
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
