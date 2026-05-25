import { useEffect, useState } from "react";
import { api, type ExplainResult } from "../api";
import { MarkdownView } from "./MarkdownView";

type Props = {
  open: boolean;
  query: string | null;
  onClose: () => void;
  onNodeClick: (label: string) => void;
};

export function ExplainDrawer({ open, query, onClose, onNodeClick }: Props) {
  const [result, setResult] = useState<ExplainResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !query) return;
    setLoading(true);
    setResult(null);
    api.explain(query)
      .then(setResult)
      .catch((e: Error) => setResult({ error: e.message }))
      .finally(() => setLoading(false));
  }, [open, query]);

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Node detail</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>

        {loading && <div className="drawer-body"><div className="spinner" /> Loading…</div>}

        {result?.error && (
          <div className="drawer-body"><p className="error-text">{result.error}</p></div>
        )}

        {result?.node && (
          <div className="drawer-body">
            <h3 className="drawer-node-label">{result.node.label}</h3>
            <div className="drawer-meta">
              {result.node.source_file && <div><strong>Source:</strong> {result.node.source_file}</div>}
              {result.node.community_label && <div><strong>Community:</strong> {result.node.community_label}</div>}
              {result.node.degree != null && <div><strong>Connections:</strong> {result.node.degree}</div>}
            </div>

            {result.explanation && (
              <section>
                <h4>Explanation</h4>
                <MarkdownView className="answer">{result.explanation}</MarkdownView>
              </section>
            )}

            {result.neighbors && result.neighbors.length > 0 && (
              <section>
                <h4>Connected to ({result.neighbors.length})</h4>
                <ul className="neighbor-list">
                  {result.neighbors.map((n, i) => (
                    <li key={i}>
                      <span className={`confidence ${n.confidence ?? ""}`}>{n.relation ?? "?"}</span>
                      <button className="link-btn" onClick={() => onNodeClick(n.label)}>{n.label}</button>
                      {n.source_file && <span className="node-src">{n.source_file}</span>}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
