import { useState } from "react";
import { api, type QueryResult, type Stats, type Insights } from "../api";
import { MarkdownView } from "./MarkdownView";

type Props = {
  stats: Stats | null;
  insights: Insights | null;
  initialQuestion?: string;
  onNodeClick: (label: string) => void;
};

export function AskPanel({ stats, insights, initialQuestion = "", onNodeClick }: Props) {
  const [question, setQuestion] = useState(initialQuestion);
  const [mode, setMode] = useState<"bfs" | "dfs">("bfs");
  const [synthesize, setSynthesize] = useState(true);
  const [webGrounding, setWebGrounding] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [querying, setQuerying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async (q?: string) => {
    const text = (q ?? question).trim();
    if (!text) return;
    if (q !== undefined) setQuestion(text);
    setQuerying(true);
    setResult(null);
    setError(null);
    try {
      const res = await api.query(text, mode, synthesize, webGrounding);
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQuerying(false);
    }
  };

  return (
    <div>
      <div className="query-box">
        <label>Ask the graph</label>
        <input
          className="query-input"
          type="text"
          placeholder="e.g. what are the strongest ideas?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !querying) ask(); }}
          disabled={querying}
        />
        <div className="query-controls">
          <div className="group">
            <button className={mode === "bfs" ? "active" : ""} onClick={() => setMode("bfs")}>BFS</button>
            <button className={mode === "dfs" ? "active" : ""} onClick={() => setMode("dfs")}>DFS</button>
          </div>
          <label>
            <input type="checkbox" checked={synthesize} onChange={(e) => setSynthesize(e.target.checked)} />
            synthesize answer
          </label>
          <label title="Let the model verify time-sensitive facts via Anthropic web_search">
            <input type="checkbox" checked={webGrounding} onChange={(e) => setWebGrounding(e.target.checked)} />
            🌐 web grounding
          </label>
          <button className="btn-primary" onClick={() => ask()} disabled={querying || !question.trim()}>
            {querying ? "Searching…" : "Ask"}
          </button>
        </div>
      </div>

      {!result && !querying && !error && (
        <>
          {insights?.questions && insights.questions.length > 0 && (
            <div className="suggested-questions">
              <h3>Suggested questions from the graph</h3>
              <ul>
                {insights.questions.slice(0, 6).map((q, i) => (
                  <li key={i}>
                    <button className="link-btn" onClick={() => ask(q.question)}>
                      {q.question}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="empty-state">
            {stats?.has_graph ? (
              <>
                <h2>Ready to query</h2>
                <p>Ask above, click a suggested question, or open any node in the Graph tab to explore.</p>
              </>
            ) : (
              <>
                <h2>Upload a document to get started</h2>
                <p>Drop PDFs, markdown, or code files into the sidebar. The graph builds automatically.</p>
              </>
            )}
          </div>
        </>
      )}

      {error && (
        <div className="answer-section">
          <h2>Error</h2>
          <div className="answer" style={{ borderLeftColor: "var(--danger)" }}>{error}</div>
        </div>
      )}

      {result && <QueryResultView result={result} onNodeClick={onNodeClick} />}
    </div>
  );
}

function QueryResultView({ result, onNodeClick }: { result: QueryResult; onNodeClick: (label: string) => void }) {
  if (result.error) {
    return (
      <div className="empty-state">
        <h2>No matching nodes</h2>
        <p>{result.error}</p>
      </div>
    );
  }
  const { subgraph, answer, start_nodes, mode, answer_error, fallback_used, gaps } = result;
  return (
    <>
      {answer && (
        <div className="answer-section">
          <h2>Answer{fallback_used && <span className="badge-muted" style={{ marginLeft: 8 }}>broad context (no term match)</span>}</h2>
          <MarkdownView className="answer">{answer}</MarkdownView>
          {gaps && gaps.length > 0 && (
            <div className="answer-gaps" aria-label="What the brain doesn't know yet">
              <div className="answer-gaps-label">⚠ What the brain doesn't know yet</div>
              <ul>
                {gaps.map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {answer_error && (
        <div className="answer-section">
          <h2>Synthesis failed</h2>
          <div className="answer" style={{ borderLeftColor: "var(--danger)" }}>{answer_error}</div>
        </div>
      )}

      <div className="subgraph">
        <div className="subgraph-header">
          <span>
            <strong>{subgraph.nodes.length}</strong> nodes ·{" "}
            <strong>{subgraph.edges.length}</strong> edges · traversal <strong>{(mode ?? "bfs").toUpperCase()}</strong>
          </span>
          {start_nodes && start_nodes.length > 0 && (
            <span>start: {start_nodes.join(", ")}</span>
          )}
        </div>
        <ul className="node-list">
          {subgraph.nodes.slice(0, 60).map((n) => (
            <li key={n.id}>
              {n.is_start && <span className="badge">START</span>}
              <span className="badge badge-relevance">{n.relevance}</span>
              <button className="node-label link-btn" onClick={() => onNodeClick(n.label)}>
                {n.label}
              </button>
              {n.source_file && <span className="node-src" title={n.source_file}>{n.source_file}</span>}
            </li>
          ))}
        </ul>
        {subgraph.edges.length > 0 && (
          <ul className="edge-list">
            {subgraph.edges.slice(0, 80).map((e, i) => (
              <li key={i}>
                {findLabel(subgraph.nodes, e.source)}
                {" "}
                <span className={`confidence ${e.confidence ?? ""}`}>{e.relation ?? "?"}</span>
                {" → "}
                {findLabel(subgraph.nodes, e.target)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

function findLabel(nodes: QueryResult["subgraph"]["nodes"], id: string): string {
  return nodes.find((n) => n.id === id)?.label ?? id;
}
