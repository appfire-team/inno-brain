import { useEffect, useState } from "react";
import { api, type TurnInfluence, type RunInfluence, type InfluenceLever } from "../api";

type Target =
  | { kind: "turn"; conversationId: string; turnIdx: number }
  | { kind: "run"; runId: string };

type Props = {
  open: boolean;
  target: Target | null;
  onClose: () => void;
  /** Called when the user clicks a re-run lever. Parent applies the settings
   * PATCH + new turn POST. Only available for conversation-turn targets. */
  onApplyLever?: (lever: InfluenceLever, question: string) => Promise<void> | void;
};

const KIND_ICON: Record<string, string> = {
  rubric: "📐",
  memory: "🧠",
  community: "🕸",
  web: "🌐",
  intent: "🎯",
  model: "🤖",
  strategy: "🪜",
  step_convergence: "🧲",
  scenario_anchor: "📌",
};

function KindBadge({ kind }: { kind: string }) {
  return (
    <span className={`influence-kind influence-kind-${kind}`}>
      <span className="influence-icon" aria-hidden>{KIND_ICON[kind] ?? "•"}</span>
      {kind.replace(/_/g, " ")}
    </span>
  );
}

function WeightDot({ weight }: { weight: string }) {
  return <span className={`influence-weight influence-weight-${weight}`}>{weight}</span>;
}

export function InfluenceDrawer({ open, target, onClose, onApplyLever }: Props) {
  const [data, setData] = useState<TurnInfluence | RunInfluence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appliedLever, setAppliedLever] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !target) return;
    setLoading(true);
    setData(null);
    setError(null);
    setAppliedLever(null);
    const fetcher = target.kind === "turn"
      ? api.explainTurnInfluence(target.conversationId, target.turnIdx)
      : api.explainRunInfluence(target.runId);
    fetcher
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [open, target]);

  if (!open || !target) return null;

  const isTurn = target.kind === "turn";
  const turnData = isTurn ? (data as TurnInfluence | null) : null;
  const runData = !isTurn ? (data as RunInfluence | null) : null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer influence-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Why this answer?</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>

        {loading && (
          <div className="drawer-body">
            <div className="spinner" /> Scoring influences…
          </div>
        )}

        {error && (
          <div className="drawer-body"><p className="error-text">{error}</p></div>
        )}

        {data && (
          <div className="drawer-body">
            {/* --- Plain-language summary ---------------------------------- */}
            {data.summary && (
              <section className="influence-summary">
                <p>{data.summary}</p>
              </section>
            )}

            {/* --- Convergence theme (playbook-only, prominent) ------------ */}
            {runData?.convergence_theme && (
              <section className="influence-convergence">
                <div className="influence-convergence-label">🧲 Cross-step convergence</div>
                <div className="influence-convergence-theme">{runData.convergence_theme}</div>
              </section>
            )}

            {/* --- Settings snapshot --------------------------------------- */}
            {turnData?.settings && (
              <section className="influence-settings">
                <h4>Settings used for this turn</h4>
                <ul className="influence-meta">
                  {turnData.settings.intent && <li><strong>Intent:</strong> {turnData.settings.intent}</li>}
                  {turnData.settings.rubric_id && <li><strong>Rubric:</strong> {turnData.settings.rubric_id}</li>}
                  {turnData.settings.inference_strategy && turnData.settings.inference_strategy !== "none" && (
                    <li><strong>Strategy:</strong> {turnData.settings.inference_strategy}</li>
                  )}
                  <li><strong>Web grounding:</strong> {turnData.settings.web_grounding ? "on" : "off"}</li>
                  {turnData.settings.answer_model && <li><strong>Model:</strong> {turnData.settings.answer_model}</li>}
                </ul>
              </section>
            )}

            {/* --- Influence list ------------------------------------------ */}
            {data.influences && data.influences.length > 0 && (
              <section>
                <h4>What pulled the answer this direction</h4>
                <ul className="influence-list">
                  {data.influences.map((inf, i) => (
                    <li key={i} className="influence-item">
                      <div className="influence-item-head">
                        <KindBadge kind={inf.kind} />
                        <WeightDot weight={inf.weight} />
                      </div>
                      <div className="influence-item-label">{inf.label}</div>
                      <div className="influence-item-evidence">{inf.evidence}</div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* --- Re-run levers (conversation only) ----------------------- */}
            {turnData && turnData.levers && turnData.levers.length > 0 && onApplyLever && (
              <section>
                <h4>What you could try differently</h4>
                <p className="muted-note influence-levers-note">
                  Each lever applies one change to this conversation's settings, then re-asks the same question.
                </p>
                <ul className="influence-levers">
                  {turnData.levers.map((lever) => (
                    <li key={lever.id}>
                      <button
                        className="influence-lever-btn"
                        disabled={appliedLever !== null}
                        onClick={async () => {
                          if (!turnData.question) return;
                          setAppliedLever(lever.id);
                          try {
                            await onApplyLever(lever, turnData.question);
                            onClose();
                          } catch (e) {
                            setError((e as Error).message);
                            setAppliedLever(null);
                          }
                        }}
                      >
                        <div className="influence-lever-label">
                          {appliedLever === lever.id ? "Re-running…" : lever.label}
                        </div>
                        <div className="influence-lever-effect">{lever.expected_effect}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* --- Read-only note (playbook) ------------------------------- */}
            {runData?.note && (
              <section className="influence-note">
                <p className="muted-note">{runData.note}</p>
              </section>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
