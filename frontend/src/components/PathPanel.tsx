import { useState } from "react";
import { api, type PathResult } from "../api";

type Props = {
  onNodeClick: (label: string) => void;
};

export function PathPanel({ onNodeClick }: Props) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [result, setResult] = useState<PathResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trace = async () => {
    if (!source.trim() || !target.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.path(source, target);
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="path-panel">
      <div className="query-box">
        <label>Trace a path between two concepts</label>
        <div className="path-inputs">
          <input
            className="query-input"
            type="text"
            placeholder="From — e.g. Opsgenie"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <span className="path-arrow">→</span>
          <input
            className="query-input"
            type="text"
            placeholder="To — e.g. Medical Device"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
          <button className="btn-primary" onClick={trace} disabled={loading || !source.trim() || !target.trim()}>
            {loading ? "Tracing…" : "Trace"}
          </button>
        </div>
      </div>

      {error && (
        <div className="answer-section">
          <h2>Error</h2>
          <div className="answer" style={{ borderLeftColor: "var(--danger)" }}>{error}</div>
        </div>
      )}

      {result?.error && (
        <div className="answer-section">
          <h2>No path</h2>
          <div className="answer" style={{ borderLeftColor: "var(--warn)" }}>{result.error}</div>
        </div>
      )}

      {result && !result.error && result.path && (
        <div className="path-result">
          <div className="subgraph-header">
            <span>
              Matched: <strong>{result.matched_source}</strong> → <strong>{result.matched_target}</strong>
            </span>
            <span>{result.hop_count} hops</span>
          </div>
          <ol className="path-chain">
            {result.path.map((hop, i) => (
              <li key={hop.id}>
                <div className="path-node">
                  <span className="hop-number">{i + 1}</span>
                  <button className="link-btn" onClick={() => onNodeClick(hop.label)}>{hop.label}</button>
                  {hop.source_file && <span className="node-src">{hop.source_file}</span>}
                </div>
                {hop.out_relation && (
                  <div className="path-edge">
                    <span className={`confidence ${hop.out_confidence ?? ""}`}>{hop.out_relation}</span>
                    {hop.out_confidence_score != null && (
                      <span className="muted-note">conf {hop.out_confidence_score.toFixed(2)}</span>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {!result && !loading && !error && (
        <div className="empty-state">
          <h2>Path tracer</h2>
          <p>Type two concept names from the corpus. Best matches in each box become the endpoints.</p>
        </div>
      )}
    </div>
  );
}
