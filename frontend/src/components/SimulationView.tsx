import { useState } from "react";
import { type Simulation } from "../api";
import { MarkdownView } from "./MarkdownView";

type Props = {
  simulation: Simulation;
};

const PERSONA_COLORS: Record<string, string> = {
  bull: "var(--extracted)",       // green
  bear: "var(--danger)",          // pink/red
  customer: "var(--accent)",      // indigo
  competitor: "var(--inferred)",  // amber
};

// Pull the "Most-likely outcome" section out of the synthesis markdown so we
// can show it as a preview when the card is collapsed. Falls back to first
// paragraph if the section header is missing.
function extractMostLikelyOutcome(synthesis: string): string {
  const re = /###\s*most[- ]likely\s+outcome\s*\n([\s\S]*?)(?:\n###|\n##|$)/i;
  const m = synthesis.match(re);
  if (m && m[1].trim()) return m[1].trim();
  // Fallback: first non-header paragraph
  const para = synthesis.split(/\n{2,}/).find((p) => p.trim() && !p.trim().startsWith("#"));
  return (para || synthesis).trim();
}

export function SimulationView({ simulation }: Props) {
  const [expanded, setExpanded] = useState(false);
  const preview = extractMostLikelyOutcome(simulation.synthesis);

  return (
    <div className={`sim-block ${expanded ? "sim-expanded" : "sim-collapsed"}`}>
      <header className="sim-header">
        <div className="sim-header-main">
          <div className="sim-kind">SCENARIO SIMULATION · {simulation.horizon_label}</div>
          <div className="sim-question">{simulation.question}</div>
          <div className="sim-personas-strip">
            {simulation.personas.map((p) => (
              <span
                key={p.key}
                className="sim-persona-pill"
                style={{
                  background: `color-mix(in srgb, ${PERSONA_COLORS[p.key] ?? "var(--accent)"} 15%, transparent)`,
                  color: PERSONA_COLORS[p.key] ?? "var(--accent)",
                  borderColor: `color-mix(in srgb, ${PERSONA_COLORS[p.key] ?? "var(--accent)"} 35%, transparent)`,
                }}
              >
                {p.label}
              </span>
            ))}
          </div>
        </div>
        <div className="sim-header-right">
          <div className="sim-meta">
            <span>{(simulation.elapsed_ms / 1000).toFixed(1)}s</span>
            <span>{simulation.tokens.input.toLocaleString()} in / {simulation.tokens.output.toLocaleString()} out</span>
          </div>
          <button
            className="btn-secondary small"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Collapse simulation" : "Expand simulation"}
          >
            {expanded ? "▾ Collapse" : "▸ Expand"}
          </button>
        </div>
      </header>

      {!expanded && (
        <div className="sim-preview">
          <div className="sim-preview-label">MOST-LIKELY OUTCOME</div>
          <MarkdownView className="sim-preview-body">{preview}</MarkdownView>
          <button className="link-btn sim-expand-link" onClick={() => setExpanded(true)}>
            Expand for the four persona viewpoints and full synthesis →
          </button>
        </div>
      )}

      {expanded && (
        <>
          <div className="sim-personas">
            {simulation.personas.map((p) => (
              <div
                key={p.key}
                className="sim-persona-card"
                style={{ borderLeftColor: PERSONA_COLORS[p.key] ?? "var(--accent)" }}
              >
                <div className="sim-persona-label" style={{ color: PERSONA_COLORS[p.key] ?? "var(--accent)" }}>
                  {p.label}
                  {p.tagline && <span className="sim-persona-tag"> · {p.tagline}</span>}
                </div>
                <MarkdownView className="sim-persona-body">{p.text}</MarkdownView>
              </div>
            ))}
          </div>

          <div className="sim-synthesis">
            <div className="sim-synthesis-label">SYNTHESIS</div>
            <MarkdownView className="sim-synthesis-body">{simulation.synthesis}</MarkdownView>
          </div>

          {simulation.entry_node_labels && simulation.entry_node_labels.length > 0 && (
            <details className="turn-trace">
              <summary>
                Grounded in {simulation.entry_node_labels.length} entry node
                {simulation.entry_node_labels.length !== 1 ? "s" : ""}
              </summary>
              <ul>
                {simulation.entry_node_labels.map((label, i) => (
                  <li key={i}>{label}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
