import { useEffect, useState } from "react";
import { api, type Insights } from "../api";

type Props = {
  onAskQuestion: (q: string) => void;
  onNodeClick: (label: string) => void;
};

export function InsightsPanel({ onAskQuestion, onNodeClick }: Props) {
  const [insights, setInsights] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.insights()
      .then(setInsights)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="empty-state"><div className="spinner" /> Loading insights…</div>;
  if (error) return <div className="empty-state"><h2>Failed to load insights</h2><p>{error}</p></div>;
  if (!insights) return null;

  const gods = insights.gods ?? [];
  const surprises = insights.surprises ?? [];
  const questions = insights.questions ?? [];

  if (gods.length === 0 && surprises.length === 0 && questions.length === 0) {
    return <div className="empty-state"><h2>No insights yet</h2><p>Upload at least one document.</p></div>;
  }

  return (
    <div className="insights-panel">
      <section className="insights-section">
        <h2>God nodes <span>· most-connected concepts</span></h2>
        {gods.length === 0 ? (
          <div className="empty">No god nodes computed.</div>
        ) : (
          <ol className="god-list">
            {gods.slice(0, 12).map((g) => (
              <li key={g.id}>
                <span className="degree-badge">{g.degree}</span>
                <button className="link-btn" onClick={() => onNodeClick(g.label)}>{g.label}</button>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="insights-section">
        <h2>Surprising connections <span>· cross-community edges</span></h2>
        {surprises.length === 0 ? (
          <div className="empty">No surprising connections.</div>
        ) : (
          <ul className="surprise-list">
            {surprises.slice(0, 12).map((s, i) => (
              <li key={i}>
                <button className="link-btn" onClick={() => onNodeClick(s.source_label ?? s.source)}>
                  {s.source_label ?? s.source}
                </button>
                <span className={`confidence ${s.confidence ?? ""}`}>{s.relation ?? "linked"}</span>
                <button className="link-btn" onClick={() => onNodeClick(s.target_label ?? s.target)}>
                  {s.target_label ?? s.target}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="insights-section">
        <h2>Suggested questions <span>· generated from the graph structure</span></h2>
        {questions.length === 0 ? (
          <div className="empty">No suggestions.</div>
        ) : (
          <ul className="question-list">
            {questions.slice(0, 10).map((q, i) => (
              <li key={i}>
                <button className="link-btn" onClick={() => onAskQuestion(q.question)}>
                  {q.question}
                </button>
                {q.reason && <div className="question-reason">{q.reason}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
