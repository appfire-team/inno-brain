import { useCallback, useEffect, useState } from "react";
import {
  api,
  type ForesightPersona,
  type ForesightSession,
  type ForesightSessionSummary,
  type ModelOption,
  type Rubric,
} from "../api";
import { MarkdownView } from "./MarkdownView";
import { PersonaLibraryDrawer } from "./PersonaLibraryDrawer";

type Prefill = {
  scenario: string;
  conversationId?: string;
  conversationTitle?: string;
} | null;

type Props = {
  prefill?: Prefill;
  onPrefillConsumed?: () => void;
  onNotify?: (kind: "success" | "error" | "info", message: string) => void;
};

export function ForeSightPanel({ prefill, onPrefillConsumed, onNotify }: Props) {
  const [sessions, setSessions] = useState<ForesightSessionSummary[]>([]);
  const [active, setActive] = useState<ForesightSession | null>(null);
  const [personas, setPersonas] = useState<ForesightPersona[]>([]);
  const [horizons, setHorizons] = useState<Record<string, string>>({});
  const [rubrics, setRubrics] = useState<Rubric[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showPersonas, setShowPersonas] = useState(false);
  const [showBuilder, setShowBuilder] = useState(false);
  const [running, setRunning] = useState(false);

  const reloadAll = useCallback(async () => {
    try {
      const [s, p, h, r, m] = await Promise.all([
        api.foresightSessions(),
        api.foresightPersonas(),
        api.foresightHorizons(),
        api.rubrics(),
        api.models(),
      ]);
      setSessions(s.sessions);
      setPersonas(p.personas);
      setHorizons(h.horizons);
      setRubrics(r.rubrics);
      setModels(m.models);
      setDefaultModel(m.default);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reloadAll();
  }, [reloadAll]);

  // If a scenario was handed over from Conversations, open the builder pre-filled.
  useEffect(() => {
    if (prefill?.scenario && prefill.scenario.trim()) {
      setShowBuilder(true);
    }
  }, [prefill]);

  const sendActiveToArtifact = useCallback(async () => {
    if (!active || !active.output) return;
    const title = (active.title || active.scenario || "Foresight brief").slice(0, 80);
    const synth = active.output.synthesis || "";
    const tldr = synth ? synth.split(/\n+/)[0].slice(0, 240) : `Foresight on: ${active.scenario.slice(0, 200)}`;
    const sections: Record<string, string> = {
      Scenario: active.scenario,
    };
    if (active.world_context) sections["World context"] = active.world_context;
    active.output.rounds.forEach((round, ri) => {
      const body = round.map((entry) => `**${entry.label}**\n${entry.text}`).join("\n\n");
      sections[`Round ${ri + 1}${ri === 0 ? " — opening positions" : " — reactions"}`] = body;
    });
    if (synth) sections.Synthesis = synth;

    const raw_markdown = [
      `# ${title}`,
      "",
      `**TL;DR:** ${tldr}`,
      "",
      `## Scenario\n${active.scenario}`,
      active.world_context ? `\n## World context\n${active.world_context}` : "",
      ...active.output.rounds.map((round, ri) =>
        `\n## Round ${ri + 1}${ri === 0 ? " — opening positions" : " — reactions"}\n` +
        round.map((entry) => `**${entry.label}**\n${entry.text}`).join("\n\n"),
      ),
      synth ? `\n## Synthesis\n${synth}` : "",
    ].filter(Boolean).join("\n");

    try {
      const art = await api.createArtifact({
        artifact_type: "ForesightBrief",
        title,
        tldr,
        sections,
        raw_markdown,
        provenance: {
          foresight_session_id: active.id,
          horizon: active.horizon,
          source: "foresight",
        },
      });
      onNotify?.("success", `Sent to Artifacts → "${art.title}"`);
    } catch (e) {
      onNotify?.("error", `Send to Artifacts failed: ${(e as Error).message}`);
    }
  }, [active, onNotify]);

  const openSession = async (sid: string) => {
    try {
      const s = await api.foresightGetSession(sid);
      setActive(s);
      setShowBuilder(false);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deleteSession = async (sid: string) => {
    if (!confirm("Delete this foresight session?")) return;
    try {
      await api.foresightDeleteSession(sid);
      if (active?.id === sid) setActive(null);
      await reloadAll();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleCreate = async (body: Parameters<typeof api.foresightCreateSession>[0]) => {
    try {
      const s = await api.foresightCreateSession(body);
      setActive(s);
      setShowBuilder(false);
      onPrefillConsumed?.();
      await reloadAll();
      // Immediately run
      await runActive(s.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const runActive = async (sid: string) => {
    setRunning(true);
    setError(null);
    try {
      const updated = await api.foresightRun(sid);
      setActive(updated);
      await reloadAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="foresight">
      <aside className="fs-sidebar">
        <div className="conv-toolbar">
          <button
            className="btn-primary small full"
            onClick={() => { setShowBuilder(true); setActive(null); }}
          >
            + New simulation
          </button>
          <button
            className="btn-secondary small full"
            onClick={() => setShowPersonas(true)}
            style={{ marginTop: 6 }}
          >
            Persona library ({personas.length})
          </button>
        </div>
        <ul className="conv-list">
          {sessions.length === 0 && <li className="empty">No simulations yet.</li>}
          {sessions.map((s) => (
            <li key={s.id} className={`conv-item ${active?.id === s.id ? "active" : ""}`}>
              <button className="conv-item-button" onClick={() => openSession(s.id)}>
                <div className="conv-title">{s.title}</div>
                <div className="conv-meta">
                  {s.persona_count} personas · {s.rounds}r · {s.horizon_label}
                  {s.status === "complete" ? " · ✓" : s.status === "running" ? " · …" : ""}
                </div>
              </button>
              <button className="conv-delete" onClick={() => deleteSession(s.id)}>×</button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="fs-main">
        {showBuilder ? (
          <SessionBuilder
            personas={personas}
            horizons={horizons}
            rubrics={rubrics}
            models={models}
            defaultModel={defaultModel}
            initialScenario={prefill?.scenario ?? ""}
            initialConversationId={prefill?.conversationId}
            initialConversationTitle={prefill?.conversationTitle}
            onCancel={() => { setShowBuilder(false); onPrefillConsumed?.(); }}
            onSubmit={handleCreate}
          />
        ) : active ? (
          <SessionView
            session={active}
            personas={personas}
            running={running}
            onRerun={() => runActive(active.id)}
            onSendToArtifact={sendActiveToArtifact}
          />
        ) : (
          <div className="empty-state fs-empty">
            <div className="fs-empty-icon">🔮</div>
            <h2>ForeSight</h2>
            <p>Pick a scenario. Personas debate it. Synthesis extracts what converges, what splits, and what to watch.</p>
            <button className="btn-primary" onClick={() => setShowBuilder(true)}>
              New simulation
            </button>
            <p className="muted-note" style={{ marginTop: 16 }}>
              Quick 4-persona debate inside a thread? Use the Simulate button in Conversations.
            </p>
          </div>
        )}
        {error && <div className="toast error" style={{ position: "fixed", bottom: 24, right: 24 }}>{error}</div>}
      </main>

      {showPersonas && (
        <PersonaLibraryDrawer
          open
          onClose={() => setShowPersonas(false)}
          onChange={reloadAll}
        />
      )}
    </div>
  );
}

// ---------- Builder ---------------------------------------------------------

function SessionBuilder({
  personas, horizons, rubrics, models, defaultModel,
  initialScenario, initialConversationId, initialConversationTitle,
  onCancel, onSubmit,
}: {
  personas: ForesightPersona[];
  horizons: Record<string, string>;
  rubrics: Rubric[];
  models: ModelOption[];
  defaultModel: string;
  initialScenario: string;
  initialConversationId?: string;
  initialConversationTitle?: string;
  onCancel: () => void;
  onSubmit: (body: Parameters<typeof api.foresightCreateSession>[0]) => void;
}) {
  const [title, setTitle] = useState("");
  const [scenario, setScenario] = useState(initialScenario);
  const [horizon, setHorizon] = useState("1y");
  const [rounds, setRounds] = useState(2);
  const [worldContext, setWorldContext] = useState("");
  const [rubricId, setRubricId] = useState(rubrics[0]?.id ?? "");
  const [useGraph, setUseGraph] = useState(true);
  const [useMemory, setUseMemory] = useState(true);
  const [webGrounding, setWebGrounding] = useState(false);
  const [synthStrategy, setSynthStrategy] = useState<"none" | "reflection" | "cove" | "best_of_3">("none");
  const [answerModel, setAnswerModel] = useState("");
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [conversationTitle] = useState<string | undefined>(initialConversationTitle);
  const [selected, setSelected] = useState<string[]>(() =>
    personas.filter((p) => ["preset:bull", "preset:bear", "preset:customer", "preset:competitor"].includes(p.id)).map((p) => p.id)
  );

  // When promoted from a conversation, fetch up to 3 candidate scenarios
  // synthesized from the thread. Chips are advisory — the user clicks one to
  // replace the scenario textarea (they can also keep editing freely).
  const [scenarioCandidates, setScenarioCandidates] = useState<string[]>([]);
  const [scenariosLoading, setScenariosLoading] = useState(false);
  useEffect(() => {
    if (!initialConversationId) return;
    let cancelled = false;
    setScenariosLoading(true);
    api.synthesizeScenariosFromConversation(initialConversationId)
      .then((r) => { if (!cancelled) setScenarioCandidates(r.scenarios ?? []); })
      .catch(() => { if (!cancelled) setScenarioCandidates([]); })
      .finally(() => { if (!cancelled) setScenariosLoading(false); });
    return () => { cancelled = true; };
  }, [initialConversationId]);

  // When personas list arrives after mount, seed the default selection.
  useEffect(() => {
    if (selected.length === 0 && personas.length > 0) {
      setSelected(
        personas
          .filter((p) => ["preset:bull", "preset:bear", "preset:customer", "preset:competitor"].includes(p.id))
          .map((p) => p.id)
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personas]);

  const toggle = (id: string) => {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };

  const canSubmit = scenario.trim() && selected.length >= 2;

  return (
    <div className="fs-builder">
      <header className="fs-builder-head">
        <h2>New foresight simulation</h2>
        <button className="btn-secondary small" onClick={onCancel}>Cancel</button>
      </header>

      {conversationId && (
        <div className="fs-linked-pill">
          <span className="fs-linked-icon">🔗</span>
          <span>
            Linked to conversation: <strong>{conversationTitle ?? conversationId}</strong>
            <span className="muted-note"> — prior turns + persistent memory will be injected as context</span>
          </span>
          <button
            className="conv-delete small"
            title="Detach this conversation from the simulation"
            onClick={() => setConversationId(undefined)}
          >×</button>
        </div>
      )}

      <div className="fs-builder-grid">
        <div className="fs-builder-form">
          <label className="modal-field">
            <span>Title (optional)</span>
            <input
              className="text-input"
              placeholder="e.g. Q3 2026 Opsgenie migration window"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="modal-field">
            <span>Scenario question</span>
            {initialConversationId && (scenariosLoading || scenarioCandidates.length > 0) && (
              <div className="fs-scenario-suggestions">
                <div className="fs-scenario-suggestions-label">
                  {scenariosLoading ? (
                    <><span className="spinner spinner-inline" /> Synthesizing scenarios from this conversation…</>
                  ) : (
                    <>💡 Candidate scenarios from this conversation — click to use</>
                  )}
                </div>
                {!scenariosLoading && scenarioCandidates.length > 0 && (
                  <div className="fs-scenario-chips">
                    {scenarioCandidates.map((s, i) => (
                      <button
                        type="button"
                        key={i}
                        className="fs-scenario-chip"
                        onClick={() => setScenario(s)}
                        title="Replace the scenario field with this"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <textarea
              className="rubric-body-input"
              rows={3}
              placeholder="What happens if Appfire ships the Opsgenie migration bundle in Q3 2026?"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              autoFocus
            />
          </label>
          <label className="modal-field">
            <span>World context (optional)</span>
            <textarea
              className="rubric-body-input"
              rows={3}
              placeholder="Assume these conditions hold during the simulation — e.g. 'Atlassian extends Opsgenie EOL by 6 months', 'CMMC enforcement begins on schedule', 'Rovo doesn't ship native on-call'."
              value={worldContext}
              onChange={(e) => setWorldContext(e.target.value)}
            />
          </label>
          <div className="fs-form-row">
            <label className="modal-field">
              <span>Horizon</span>
              <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
                {Object.entries(horizons).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="modal-field">
              <span>Debate rounds</span>
              <select value={rounds} onChange={(e) => setRounds(Number(e.target.value))}>
                <option value={1}>1 (parallel only)</option>
                <option value={2}>2 (react once)</option>
                <option value={3}>3 (react twice)</option>
              </select>
            </label>
          </div>
          <div className="fs-form-row">
            <label className="modal-field">
              <span>Rubric</span>
              <select value={rubricId} onChange={(e) => setRubricId(e.target.value)}>
                <option value="">— none —</option>
                {rubrics.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </label>
            <label className="modal-field">
              <span>Model</span>
              <select value={answerModel} onChange={(e) => setAnswerModel(e.target.value)}>
                <option value="">default ({models.find((m) => m.id === defaultModel)?.label ?? "Sonnet 4.6"})</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}{m.hint ? ` — ${m.hint}` : ""}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="modal-field">
            <span>Synthesizer inference strategy</span>
            <select
              value={synthStrategy}
              onChange={(e) => setSynthStrategy(e.target.value as typeof synthStrategy)}
              title="Wraps only the final synthesis pass — persona rounds always run normally."
            >
              <option value="none">none (single pass — fastest)</option>
              <option value="reflection">reflection (draft → critique → revise)</option>
              <option value="cove">chain-of-verification (draft → verify → revise)</option>
              <option value="best_of_3">best of 3 (sample 3 → pick best)</option>
            </select>
            <small className="muted-note">
              Applies to the synthesis only. Persona debate rounds are unchanged.
              {conversationId && " Linked conversation's intent is also applied to the synthesizer."}
            </small>
          </label>
          <label className="modal-field row">
            <input
              type="checkbox"
              checked={useGraph}
              onChange={(e) => setUseGraph(e.target.checked)}
            />
            <div>
              <span style={{ display: "block" }}>Ground personas in the knowledge graph</span>
              <small className="muted-note">All personas see the same subgraph slice.</small>
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
              <small className="muted-note">Durable facts about the team get prepended to every persona's prompt.</small>
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
              <small className="muted-note">Personas + synthesizer may use Anthropic web_search to verify time-sensitive facts. Slightly slower + costlier.</small>
            </div>
          </label>
        </div>

        <div className="fs-persona-picker">
          <div className="fs-persona-picker-head">
            <h3>Personas ({selected.length} selected)</h3>
            <span className="muted-note">click to toggle</span>
          </div>
          <ul className="fs-persona-list">
            {personas.map((p) => (
              <li
                key={p.id}
                className={`fs-persona-row ${selected.includes(p.id) ? "selected" : ""}`}
                onClick={() => toggle(p.id)}
              >
                <span className="legend-swatch" style={{ background: p.color ?? "var(--accent)" }} />
                <span className="fs-persona-info">
                  <span className="fs-persona-label-name">{p.label}</span>
                  {p.tagline && <span className="fs-persona-tagline">{p.tagline}</span>}
                </span>
                <span className={`fs-persona-source fs-persona-source-${p.source}`}>{p.source}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <footer className="fs-builder-foot">
        <span className="muted-note">
          {selected.length < 2
            ? "Pick at least 2 personas."
            : `Estimated cost: ~$${(0.02 * selected.length * rounds + 0.05).toFixed(2)} · ~${Math.round((10 + 15 * selected.length * rounds) / 4)}s elapsed`}
        </span>
        <button
          className="btn-primary"
          disabled={!canSubmit}
          onClick={() =>
            onSubmit({
              title: title.trim() || scenario.slice(0, 60),
              scenario,
              horizon,
              persona_ids: selected,
              rounds,
              world_context: worldContext,
              rubric_id: rubricId || null,
              use_graph: useGraph,
              answer_model: answerModel || null,
              source_conversation_id: conversationId ?? null,
              source_conversation_title: conversationId ? (conversationTitle ?? null) : null,
              use_memory: useMemory,
              web_grounding: webGrounding,
              synth_inference_strategy: synthStrategy,
            })
          }
        >
          Run simulation
        </button>
      </footer>
    </div>
  );
}

// ---------- Session view ---------------------------------------------------

function SessionView({
  session, personas, running, onRerun, onSendToArtifact,
}: {
  session: ForesightSession;
  personas: ForesightPersona[];
  running: boolean;
  onRerun: () => void;
  onSendToArtifact?: () => void | Promise<void>;
}) {
  const personaById = Object.fromEntries(personas.map((p) => [p.id, p]));
  const [reading, setReading] = useState(false);
  const [collapsedRounds, setCollapsedRounds] = useState<Record<number, boolean>>({});
  const [copied, setCopied] = useState(false);

  // Exit reading view on Esc.
  useEffect(() => {
    if (!reading) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setReading(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reading]);

  const synthesis = session.output?.synthesis || "";
  const tldr = synthesis ? firstSentence(synthesis) : "";
  const highlights = synthesis ? extractHighlights(synthesis) : [];

  const buildMarkdown = (): string => {
    const lines: string[] = [
      `# ${session.title || session.scenario.slice(0, 80)}`,
      "",
      tldr ? `**TL;DR:** ${tldr}` : "",
      "",
      `## Scenario\n${session.scenario}`,
    ];
    if (session.world_context) lines.push(`\n## World context\n${session.world_context}`);
    (session.output?.rounds || []).forEach((round, ri) => {
      lines.push(`\n## Round ${ri + 1}${ri === 0 ? " — opening positions" : " — reactions"}`);
      round.forEach((entry) => {
        lines.push(`\n**${entry.label}**\n${entry.text}`);
      });
    });
    if (synthesis) lines.push(`\n## Synthesis\n${synthesis}`);
    return lines.filter((l) => l !== "").join("\n");
  };

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(buildMarkdown());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* noop */ }
  };

  const downloadMarkdown = () => {
    const blob = new Blob([buildMarkdown()], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const safe = (session.title || "foresight").replace(/[^a-z0-9-_]+/gi, "_");
    a.href = url;
    a.download = `${safe}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const isComplete = session.status === "complete" && !!session.output;

  return (
    <div className={`fs-session pb-final-brief ${reading ? "pb-run-reading" : ""}`}>
      <header className="pb-final-brief-head">
        <div>
          <span className="muted-note" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.05 }}>
            ForeSight · {session.rounds}-round debate
          </span>
          <h2>{session.title || session.scenario.slice(0, 80)}</h2>
          <ScenarioBlock scenario={session.scenario} />
          <div className="fs-session-meta">
            <span>{session.personas.length} personas</span>
            <span>{session.horizon}</span>
            {isComplete && session.output && (
              <>
                <span>{(session.output.elapsed_ms / 1000).toFixed(1)}s</span>
                <span>{session.output.tokens.input.toLocaleString()} in · {session.output.tokens.output.toLocaleString()} out</span>
                {session.output.used_conversation_history && <span className="grounding-badge grounded">conversation</span>}
                {session.output.used_memory && <span className="grounding-badge memory">memory</span>}
                {session.output.entry_node_labels && session.output.entry_node_labels.length > 0 && (
                  <span className="grounding-badge grounded">graph</span>
                )}
              </>
            )}
          </div>
        </div>
        <div className="pb-artifact-actions">
          {isComplete && (
            <button
              className="btn-secondary small"
              onClick={() => setReading((x) => !x)}
              title="Hide chrome — show only the brief (Esc to exit)"
            >
              📖 Reading
            </button>
          )}
          {isComplete && (
            <button className="btn-secondary small" onClick={copyMarkdown}>
              {copied ? "✓ Copied" : "Copy MD"}
            </button>
          )}
          {isComplete && (
            <button className="btn-secondary small" onClick={downloadMarkdown}>⇩ .md</button>
          )}
          {onSendToArtifact && isComplete && (
            <button
              className="btn-secondary small"
              onClick={() => { void onSendToArtifact(); }}
              title="Save as Artifact"
            >
              📎 Artifact
            </button>
          )}
          <button
            className="btn-secondary small"
            onClick={onRerun}
            disabled={running}
          >
            {running ? <><span className="spinner" /> Running…</> : isComplete ? "↻ Re-run" : "▶ Run"}
          </button>
        </div>
      </header>

      {session.source_conversation_id && (
        <div className="fs-linked-pill">
          <span className="fs-linked-icon">🔗</span>
          <span>
            Linked to <strong>{session.source_conversation_title ?? session.source_conversation_id}</strong>
            {session.output?.used_conversation_history
              ? <span className="muted-note"> — prior turns injected.</span>
              : <span className="muted-note"> — run to inject context.</span>}
          </span>
        </div>
      )}

      {running && (!session.output || session.output.rounds.length < session.rounds) && (
        <div className="processing-banner">
          <span className="spinner" />
          {(session.output?.rounds.length ?? 0) === 0
            ? "Round 1: opening positions…"
            : `Round ${(session.output!.rounds.length) + 1} of ${session.rounds}: reactions…`}
          {(session.output?.rounds.length ?? 0) >= session.rounds && " — synthesizing…"}
        </div>
      )}

      <div className="pb-brief-body">
        <div className="pb-brief-prose">
          {(tldr || highlights.length > 0) && (
            <div className="pb-hero">
              <div className="pb-hero-eyebrow">TL;DR</div>
              <div className="pb-hero-tldr">
                {tldr || <span className="muted-note">Awaiting synthesis.</span>}
              </div>
              {highlights.length > 0 && (
                <div className="pb-highlights">
                  {highlights.map((h, i) => (
                    <div key={i} className={`pb-highlight pb-highlight-${h.tone}`}>
                      <span className="pb-highlight-icon">{highlightIcon(h.tone)}</span>
                      <span className="pb-highlight-text">{h.text}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {session.world_context && (
            <div className="fs-world-context">
              <div className="fs-world-label">WORLD CONTEXT</div>
              <div>{session.world_context}</div>
            </div>
          )}

          <div className="pb-sections">
            {(session.output?.rounds || []).map((round, ri) => {
              const collapsed = !!collapsedRounds[ri];
              return (
                <section key={ri} className={collapsed ? "pb-section-collapsed" : ""}>
                  <h3 onClick={() => setCollapsedRounds((s) => ({ ...s, [ri]: !collapsed }))}>
                    <button
                      className="pb-section-toggle"
                      onClick={(e) => {
                        e.stopPropagation();
                        setCollapsedRounds((s) => ({ ...s, [ri]: !collapsed }));
                      }}
                      aria-label={collapsed ? "Expand" : "Collapse"}
                    >
                      {collapsed ? "▸" : "▾"}
                    </button>
                    <span className="pb-section-num">{ri + 1}.</span>{" "}
                    <span className="pb-section-icon" aria-hidden>{ri === 0 ? "🎯" : "💬"}</span>{" "}
                    Round {ri + 1}
                    <span className="muted-note" style={{ marginLeft: 8, fontWeight: 400 }}>
                      {ri === 0 ? "opening positions" : "reactions"}
                    </span>
                  </h3>
                  {!collapsed && (
                    <div className="pb-section-body">
                      <div className="fs-round-grid">
                        {round.map((entry) => {
                          const persona = personaById[entry.persona_id];
                          const color = entry.color ?? persona?.color ?? "var(--accent)";
                          return (
                            <div
                              key={entry.persona_id}
                              className="sim-persona-card"
                              style={{ borderLeftColor: color }}
                            >
                              <div className="sim-persona-label" style={{ color }}>
                                {entry.label}
                                {persona?.tagline && <span className="sim-persona-tag"> · {persona.tagline}</span>}
                              </div>
                              <MarkdownView className="sim-persona-body">{entry.text}</MarkdownView>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </section>
              );
            })}

            {synthesis && (
              <section className="fs-synthesis-card">
                <h3>
                  <span className="pb-section-icon" aria-hidden>🧭</span> Synthesis
                </h3>
                <div className="pb-section-body">
                  <MarkdownView>{synthesis}</MarkdownView>
                </div>
              </section>
            )}
          </div>

          {session.output?.entry_node_labels && session.output.entry_node_labels.length > 0 && (
            <details className="turn-trace">
              <summary>Grounded in {session.output.entry_node_labels.length} graph nodes</summary>
              <ul>
                {session.output.entry_node_labels.map((label, i) => <li key={i}>{label}</li>)}
              </ul>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Helpers ---------------------------------------------------------

function firstSentence(text: string): string {
  // Strip Markdown headings/bullets, take the first complete sentence (up to 240 chars).
  const cleaned = text
    .replace(/^#+\s*/gm, "")
    .replace(/^[-*+]\s*/gm, "")
    .replace(/\*\*/g, "")
    .trim();
  const m = cleaned.match(/^[\s\S]*?[.!?](?=\s|$)/);
  const sentence = (m ? m[0] : cleaned.split("\n")[0]).trim();
  return sentence.length > 240 ? sentence.slice(0, 237) + "…" : sentence;
}

type HighlightTone = "win" | "risk" | "claim" | "tension" | "number";

function highlightIcon(tone: HighlightTone): string {
  switch (tone) {
    case "win": return "✓";
    case "risk": return "⚠";
    case "tension": return "⚡";
    case "number": return "#";
    default: return "•";
  }
}

// Best-effort: pull short bullets from synthesis sections named like
// "Convergent / Divergent / Signals" and tag them.
function extractHighlights(text: string): { text: string; tone: HighlightTone }[] {
  const out: { text: string; tone: HighlightTone }[] = [];
  const lines = text.split("\n");
  let currentTone: HighlightTone | null = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const heading = line.match(/^#+\s*(.+?)\s*:?\s*$/);
    if (heading) {
      const h = heading[1].toLowerCase();
      if (/converg/.test(h)) currentTone = "win";
      else if (/diverg|tension|conflict/.test(h)) currentTone = "tension";
      else if (/risk|warning|watch.?out/.test(h)) currentTone = "risk";
      else if (/signal|watch|monitor/.test(h)) currentTone = "claim";
      else currentTone = null;
      continue;
    }
    const bullet = line.match(/^[-*+]\s+(.+)/);
    if (bullet && currentTone) {
      const t = bullet[1].replace(/\*\*/g, "").trim();
      if (t.length > 0 && t.length < 180) out.push({ text: t, tone: currentTone });
      if (out.length >= 6) break;
    }
  }
  return out;
}

// Playbook-step foresight sessions inject the entire prior-step transcript
// into the scenario string as "<scenario>\n\nFindings so far:\n\n<transcript>".
// That transcript can be 30-40 KB of markdown — rendered as plain text in a
// single div it produces an 8000+ px wall the user can't scan. Split the two
// apart: show the user-authored scenario verbatim, hide the auto-injected
// transcript behind a collapsed <details> with markdown rendering when
// expanded.
function ScenarioBlock({ scenario }: { scenario: string }) {
  const splitMarker = /\n+Findings so far:\s*\n+/i;
  const m = splitMarker.exec(scenario);
  const userScenario = (m ? scenario.slice(0, m.index) : scenario).trim();
  const priorContext = m ? scenario.slice(m.index + m[0].length).trim() : "";

  // For non-playbook sessions (no "Findings so far:" marker) but with a
  // genuinely long scenario, still collapse anything past ~600 chars so a
  // pasted-in long scenario doesn't push the rounds below the fold.
  if (!priorContext && userScenario.length > 600) {
    return (
      <details className="fs-scenario-collapsible">
        <summary>
          <span className="fs-scenario-preview">{userScenario.slice(0, 240)}…</span>
          <span className="fs-scenario-toggle">Show full scenario</span>
        </summary>
        <div className="fs-scenario-full">
          <MarkdownView>{userScenario}</MarkdownView>
        </div>
      </details>
    );
  }

  return (
    <>
      <div className="fs-session-scenario">{userScenario}</div>
      {priorContext && (
        <details className="fs-scenario-collapsible">
          <summary>
            <span className="fs-scenario-toggle">
              + Show playbook context fed to personas ({Math.round(priorContext.length / 1000)}k chars)
            </span>
          </summary>
          <div className="fs-scenario-full">
            <MarkdownView>{priorContext}</MarkdownView>
          </div>
        </details>
      )}
    </>
  );
}
