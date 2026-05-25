import { useEffect, useState } from "react";
import { api } from "../api";

type PersonaInfo = { key: string; label: string; tagline: string };

type Props = {
  open: boolean;
  conversationId: string;
  initialQuestion?: string;
  onClose: () => void;
  onComplete: () => void; // re-fetch the conversation after simulate finishes
};

export function SimulateModal({ open, conversationId, initialQuestion = "", onClose, onComplete }: Props) {
  const [question, setQuestion] = useState(initialQuestion);
  const [horizon, setHorizon] = useState("1y");
  const [useGraph, setUseGraph] = useState(true);
  const [useMemory, setUseMemory] = useState(true);
  const [webGrounding, setWebGrounding] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  const [horizons, setHorizons] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setQuestion(initialQuestion);
    setError(null);
    api.simulatePersonas()
      .then((r) => {
        setPersonas(r.personas);
        setHorizons(r.horizons);
      })
      .catch((e: Error) => setError(e.message));
  }, [open, initialQuestion]);

  const run = async () => {
    if (!question.trim() || running) return;
    setRunning(true);
    setError(null);
    try {
      await api.simulate(conversationId, question.trim(), horizon, {
        useGraph,
        useMemory,
        webGrounding,
      });
      onComplete();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Simulate scenario</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>
        <div className="modal-body">
          <p className="muted-note" style={{ marginBottom: 4 }}>
            Four perspectives run in parallel — Bull, Bear, Customer, Competitor — then a
            synthesizer reconciles them into a most-likely outcome plus signals to watch.
          </p>

          <label className="modal-field">
            <span>Scenario question</span>
            <textarea
              className="rubric-body-input"
              rows={3}
              placeholder="What happens if we ship the Opsgenie-migration bundle in Q3 2026?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              autoFocus
            />
          </label>

          <label className="modal-field">
            <span>Time horizon</span>
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              {Object.entries(horizons).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>

          <label className="modal-field row">
            <input
              type="checkbox"
              checked={useGraph}
              onChange={(e) => setUseGraph(e.target.checked)}
            />
            <div>
              <span style={{ display: "block" }}>Ground personas in the knowledge graph</span>
              <small className="muted-note">
                Each persona sees the same relevant subgraph slice before answering.
              </small>
            </div>
          </label>

          <label className="modal-field row">
            <input
              type="checkbox"
              checked={useMemory}
              onChange={(e) => setUseMemory(e.target.checked)}
            />
            <div>
              <span style={{ display: "block" }}>Inject persistent memory</span>
              <small className="muted-note">
                Durable facts about the team get prepended to each persona's system prompt.
              </small>
            </div>
          </label>

          <label className="modal-field row">
            <input
              type="checkbox"
              checked={webGrounding}
              onChange={(e) => setWebGrounding(e.target.checked)}
            />
            <div>
              <span style={{ display: "block" }}>🌐 Web grounding</span>
              <small className="muted-note">
                Personas + synthesizer may use Anthropic web_search to verify time-sensitive facts. Slightly slower + costlier.
              </small>
            </div>
          </label>

          <small className="muted-note">
            Inherits the host conversation's <strong>rubric</strong>, <strong>intent</strong>, and <strong>inference strategy</strong> — set those in the conversation header.
          </small>

          {personas.length > 0 && (
            <div className="sim-roster">
              <small className="muted-note">Running:</small>
              <ul>
                {personas.map((p) => (
                  <li key={p.key}>
                    <strong>{p.label}</strong>
                    <span className="muted-note"> — {p.tagline}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && <div className="error-text" style={{ marginTop: 12 }}>{error}</div>}
        </div>
        <footer className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
          <button className="btn-primary" disabled={!question.trim() || running} onClick={run}>
            {running ? (<><span className="spinner" /> Simulating… (~30-45s)</>) : "Run simulation"}
          </button>
        </footer>
      </div>
    </div>
  );
}
