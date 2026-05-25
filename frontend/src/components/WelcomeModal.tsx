import { useEffect, useState } from "react";

// Full-screen welcome shown only on a browser's first visit. Intercepts new
// users so they start by creating their own workspace instead of landing on
// whatever workspace happens to be active on the server.
//
// State is browser-scoped (localStorage). After the user clicks either CTA,
// the flag is set and the welcome never reappears.

const LS_WELCOMED_KEY = "innobrain.user_initialized";

type Props = {
  onCreateWorkspace: () => void;
  /** Bumped by the header's "Quickstart" button to force-show the welcome
   *  dialog again — bypasses the localStorage flag so returning users can
   *  re-run the intro any time. */
  reopenTick?: number;
};

export function WelcomeModal({ onCreateWorkspace, reopenTick }: Props) {
  const [visible, setVisible] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(LS_WELCOMED_KEY) !== "1";
    } catch {
      return false;
    }
  });

  // External "reopen" signal — header Quickstart button bumps this. We don't
  // touch the localStorage flag here; the user opted in by clicking, but the
  // flag should still be considered set so future page loads don't re-show.
  useEffect(() => {
    if (reopenTick === undefined || reopenTick === 0) return;
    setVisible(true);
  }, [reopenTick]);

  // Trap Escape to dismiss-as-existing (treats as "I already have one").
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleHaveOne();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  if (!visible) return null;

  const persist = () => {
    try {
      window.localStorage.setItem(LS_WELCOMED_KEY, "1");
    } catch {}
  };

  const handleCreate = () => {
    persist();
    setVisible(false);
    onCreateWorkspace();
  };

  const handleHaveOne = () => {
    persist();
    setVisible(false);
  };

  return (
    <div className="welcome-overlay">
      <div className="welcome-card">
        <div className="welcome-mark">
          <span className="welcome-dot" />
          <span className="welcome-brand">InnoBrain</span>
        </div>
        <h1 className="welcome-h1">
          Welcome.<br />
          Let's set up <span className="welcome-accent">your team's strategy brain</span>.
        </h1>
        <p className="welcome-lead">
          Every strategy brain starts with a workspace — a scoped home for your team's
          documents, expertise, and decisions. Give it a name, drop in your docs,
          and the brain takes shape from there.
        </p>
        <div className="welcome-globals">
          <div className="welcome-glob">
            <span className="welcome-glob-icon">📐</span>
            <div>
              <strong>Rubric is per-workspace</strong>
              <span>Pick an existing rubric to seed the new workspace, or start with a fresh built-in.</span>
            </div>
          </div>
          <div className="welcome-glob">
            <span className="welcome-glob-icon">🧠</span>
            <div>
              <strong>Memory is per-workspace</strong>
              <span>Each workspace remembers its own durable facts — no cross-talk between teams.</span>
            </div>
          </div>
        </div>
        <div className="welcome-actions">
          <button className="welcome-cta-primary" onClick={handleCreate}>
            Create your workspace →
          </button>
          <button className="welcome-cta-secondary" onClick={handleHaveOne}>
            I have one already
          </button>
        </div>
        <p className="welcome-footnote">
          You can create more workspaces any time from the header. Press <kbd>Esc</kbd> to skip.
        </p>
      </div>
    </div>
  );
}
