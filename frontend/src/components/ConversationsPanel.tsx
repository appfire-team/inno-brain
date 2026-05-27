import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Conversation, type ConversationSummary, type InfluenceLever, type ModelOption, type Pin, type Rubric, type Turn } from "../api";
import type { RefinePrefill } from "./RefineKBPanel";
import { RubricManager } from "./RubricManager";
import { MemoryDrawer } from "./MemoryDrawer";
import { MarkdownView } from "./MarkdownView";
import { SimulationView } from "./SimulationView";
import { IntentSelect } from "./IntentSelect";
import { InfluenceDrawer } from "./InfluenceDrawer";
import { useCollapsed } from "../hooks/useCollapsed";

const INFERENCE_LABELS: Record<string, string> = {
  none: "Standard (single pass)",
  reflection: "Reflection (critique + revise)",
  cove: "Chain-of-verification",
  best_of_3: "Best of 3 samples",
};

function modelLabel(models: ModelOption[], id: string): string {
  return models.find((m) => m.id === id)?.label ?? id;
}

type IntentGroup = { label: string; intents: Record<string, string> };

type Props = {
  onNodeClick: (label: string) => void;
  wideMode: boolean;
  onToggleWideMode: () => void;
  onBusyChange?: (busy: boolean) => void;
  onAdvancedSimulate?: (scenario: string, conversationId?: string, conversationTitle?: string) => void;
  onSaveAsKBFact?: (prefill: RefinePrefill) => void;
  onNotify?: (kind: "success" | "error" | "info", message: string) => void;
};

export function ConversationsPanel({ onNodeClick, wideMode, onToggleWideMode, onBusyChange, onAdvancedSimulate, onSaveAsKBFact, onNotify }: Props) {
  const [list, setList] = useState<ConversationSummary[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);

  // Surface any in-flight async work (turn send, etc.) up to App so the tab
  // can show a progress indicator even when the user is on another tab.
  useEffect(() => { onBusyChange?.(sending); }, [sending, onBusyChange]);
  const [error, setError] = useState<string | null>(null);
  const [showRubrics, setShowRubrics] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [threadsCollapsed, toggleThreads] = useCollapsed("conv-threads", false);
  const [pinsCollapsed, togglePins] = useCollapsed("conv-pins", false);
  const [hideDiagnostics, toggleDiagnostics] = useCollapsed("conv-hide-diagnostics", false);
  const [activeTurnIdx, setActiveTurnIdx] = useState<number | null>(null);
  const [influenceTurnIdx, setInfluenceTurnIdx] = useState<number | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportText, setExportText] = useState<string | null>(null);
  const [intents, setIntents] = useState<Record<string, string>>({});
  const [intentGroups, setIntentGroups] = useState<Array<{ label: string; intents: Record<string, string> }>>([]);
  const [rubrics, setRubrics] = useState<Rubric[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>("");
  const turnsEndRef = useRef<HTMLDivElement>(null);

  const reloadList = useCallback(async () => {
    try {
      const r = await api.conversations();
      setList(r.conversations);
      return r.conversations;
    } catch (e) {
      setError((e as Error).message);
      return [];
    }
  }, []);

  const reloadIntentsRubrics = useCallback(async () => {
    try {
      const [i, r, m] = await Promise.all([api.intents(), api.rubrics(), api.models()]);
      setIntents(i.intents);
      // Backend now returns groups with an array of {id, label, source}; older
      // callers expected Record<string, string>. Normalize defensively.
      const normalizedGroups = (i.groups ?? []).map((g) => ({
        label: g.label,
        intents: Array.isArray(g.intents)
          ? Object.fromEntries(g.intents.map((it) => [it.id, it.label]))
          : (g.intents as Record<string, string>),
      }));
      setIntentGroups(normalizedGroups);
      setRubrics(r.rubrics);
      setModels(m.models);
      setDefaultModel(m.default);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reloadIntentsRubrics();
    reloadList().then((convs) => {
      if (convs.length > 0 && !active) openConv(convs[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    turnsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active?.turns.length, active?.id]);

  const openConv = async (id: string) => {
    try {
      const c = await api.getConversation(id);
      setActive(c);
      setComposer("");
      setExportOpen(false);
      setExportText(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Inline-edit state for the active conversation's title. `null` means
  // not-editing; a string means we're showing the input bound to this draft.
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const commitTitle = async () => {
    if (!active || titleDraft === null) return;
    const next = titleDraft.trim();
    setTitleDraft(null);
    if (!next || next === active.title) return;
    try {
      const updated = await api.renameConversation(active.id, next);
      setActive(updated);
      await reloadList();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Create a conversation with sensible defaults and jump straight into the
  // chat. Per-conversation settings (intent, model, strategy, etc.) are
  // accessible from the in-thread settings panel after the conversation is
  // open — no upfront form needed.
  const startConv = async () => {
    try {
      const c = await api.createConversation({
        title: "Untitled",
        intent: "explore",
        rubric_id: rubrics[0]?.id ?? null,
        inference_strategy: "none",
        web_grounding: true,
        auto_memory: true,
        answer_model: null,
      });
      await reloadList();
      setActive(c);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const send = async () => {
    if (!active || !composer.trim() || sending) return;
    const text = composer.trim();
    setSending(true);
    setComposer("");
    setActive({
      ...active,
      turns: [...active.turns, { role: "user", text, ts: Date.now() / 1000 } as Turn],
    });
    try {
      const updated = await api.addTurn(active.id, text);
      setActive(updated);
      await reloadList();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  /** Apply a lever from the InfluenceDrawer: patch conv settings, then re-ask
   * the same question. The drawer closes itself afterward; we update the
   * conversation view as the new turn arrives. */
  const applyInfluenceLever = useCallback(async (lever: InfluenceLever, question: string) => {
    if (!active || sending) return;
    const settings = (lever.change?.settings ?? {}) as Parameters<typeof api.updateConversationSettings>[1];
    setSending(true);
    try {
      // Optimistic settings update — the PATCH response is the full conv.
      const afterSettings = await api.updateConversationSettings(active.id, settings);
      setActive({
        ...afterSettings,
        turns: [...afterSettings.turns, { role: "user", text: question, ts: Date.now() / 1000 } as Turn],
      });
      const afterTurn = await api.addTurn(active.id, question);
      setActive(afterTurn);
      await reloadList();
      onNotify?.("info", `Re-ran with: ${lever.label}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }, [active, sending, onNotify]);

  const deleteConv = async (id: string) => {
    if (!confirm("Delete this conversation?")) return;
    try {
      await api.deleteConversation(id);
      if (active?.id === id) setActive(null);
      await reloadList();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const updateSettings = async (settings: {
    intent?: string | null;
    rubric_id?: string | null;
    inference_strategy?: string;
    web_grounding?: boolean;
    auto_memory?: boolean;
  }) => {
    if (!active) return;
    try {
      const updated = await api.updateConversationSettings(active.id, settings);
      setActive(updated);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const doExport = async () => {
    if (!active) return;
    setExportOpen(true);
    setExportText(null);
    try {
      const r = await api.exportConversation(active.id);
      setExportText(r.markdown);
    } catch (e) {
      setError((e as Error).message);
      setExportText(`(export failed: ${(e as Error).message})`);
    }
  };

  const downloadExport = () => {
    if (!active || !exportText) return;
    const blob = new Blob([exportText], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const safe = active.title.replace(/[^a-z0-9-_]+/gi, "_");
    a.href = url;
    a.download = `${safe || "conversation"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Pull the same exec-Markdown export the download button uses, then save it
  // as a ConversationReport artifact with the conversation id in provenance.
  // The artifact's "↻ Re-sync from conversation" button uses that link to push
  // a new version when the conversation grows.
  const [savingReport, setSavingReport] = useState(false);
  const sendReportToArtifact = async () => {
    if (!active || savingReport) return;
    setSavingReport(true);
    try {
      const exported = await api.exportConversation(active.id);
      const md = exported.markdown || "";
      // Extract a short TL;DR from the markdown for the artifact hero.
      const tldrMatch =
        md.match(/\*\*TL;DR[:\s]+\*\*\s*(.+?)(?=\n\n|\n#|$)/is) ||
        md.match(/^#{1,3}\s*TL;DR\s*\n+(.+?)(?=\n\n|\n#|$)/im);
      const tldr = (tldrMatch?.[1] ?? md.split(/\n\n+/).find((p) => p.trim() && !p.startsWith("#")) ?? "")
        .trim()
        .slice(0, 400);
      const title = (active.title || "Conversation report").slice(0, 80);
      const art = await api.createArtifact({
        artifact_type: "ConversationReport",
        title,
        tldr,
        sections: {},
        raw_markdown: md,
        provenance: {
          conversation_id: active.id,
          source_conversation_title: active.title,
          source: "conversation_report",
        },
      });
      onNotify?.("success", `Saved report → Artifacts: "${art.title}"`);
    } catch (e) {
      onNotify?.("error", `Save report failed: ${(e as Error).message}`);
    } finally {
      setSavingReport(false);
    }
  };

  const sendTurnToArtifact = async (
    answer: Extract<Turn, { role: "assistant" }>,
    userQuestion: string,
  ) => {
    if (!active) return;
    const title = (userQuestion || active.title || "Conversation note").slice(0, 80);
    const tldr = answer.text.split(/\n+/)[0].slice(0, 240);
    const sections: Record<string, string> = {};
    if (userQuestion) sections.Question = userQuestion;
    sections.Answer = answer.text;
    if (answer.entry_node_labels?.length) {
      sections["Routed through"] = answer.entry_node_labels.map((l) => `- ${l}`).join("\n");
    }
    const raw_markdown = [
      `# ${title}`,
      "",
      `**TL;DR:** ${tldr}`,
      "",
      userQuestion ? `## Question\n${userQuestion}\n` : "",
      `## Answer\n${answer.text}`,
    ].filter(Boolean).join("\n");
    try {
      const art = await api.createArtifact({
        artifact_type: "ConversationNote",
        title,
        tldr,
        sections,
        raw_markdown,
        provenance: {
          conversation_id: active.id,
          conversation_title: active.title,
          source: "conversation",
        },
      });
      onNotify?.("success", `Sent to Artifacts → "${art.title}"`);
    } catch (e) {
      onNotify?.("error", `Send to Artifacts failed: ${(e as Error).message}`);
    }
  };

  const pinAnswer = async (turn: Extract<Turn, { role: "assistant" }>) => {
    if (!active) return;
    try {
      const updated = await api.addPin(active.id, { kind: "answer", text: turn.text.slice(0, 240) });
      setActive(updated);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const pinNode = async (nodeId: string, label: string) => {
    if (!active) return;
    try {
      const updated = await api.addPin(active.id, { kind: "node", node_id: nodeId, label });
      setActive(updated);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const unpin = async (pin: Pin) => {
    if (!active) return;
    try {
      const updated = await api.removePin(active.id, pin.id);
      setActive(updated);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const activeRubric = active?.rubric_id ? rubrics.find((r) => r.id === active.rubric_id) : null;

  const userTurns = (active?.turns ?? [])
    .map((t, i) => ({ turn: t, idx: i }))
    .filter((x) => x.turn.role === "user");
  const hasQuestions = userTurns.length > 0;
  const hasPins = !!active && active.pins.length > 0;
  // Show the right rail whenever the active conversation has either questions
  // (outline) or pins. Old conversations get the outline for free since it's
  // derived from `turns` — no schema change.
  const showPinsRail = !!active && (hasQuestions || hasPins);
  const containerClass = [
    "conversations",
    threadsCollapsed ? "threads-collapsed" : "",
    showPinsRail && pinsCollapsed ? "pins-collapsed" : "",
    !showPinsRail ? "no-pins" : "",
    hideDiagnostics ? "diagnostics-hidden" : "",
  ].filter(Boolean).join(" ");

  const jumpToTurn = (idx: number) => {
    const el = document.getElementById(`turn-${active?.id}-${idx}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveTurnIdx(idx);
    }
  };

  useEffect(() => {
    if (!active || userTurns.length === 0) return;
    const targets = userTurns
      .map(({ idx }) => document.getElementById(`turn-${active.id}-${idx}`))
      .filter((el): el is HTMLElement => el != null);
    if (targets.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        const first = visible[0];
        if (first) {
          const id = (first.target as HTMLElement).id;
          const m = id.match(/turn-.*-(\d+)$/);
          if (m) setActiveTurnIdx(parseInt(m[1], 10));
        }
      },
      { rootMargin: "-10% 0px -70% 0px", threshold: 0 },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [active?.id, active?.turns.length]);

  return (
    <div className={containerClass}>
      {threadsCollapsed ? (
        <aside className="conv-sidebar conv-sidebar-collapsed">
          <button className="rail-toggle" onClick={toggleThreads} title="Expand threads">›</button>
          <div className="rail-meta">
            <span className="rail-count">{list.length}</span>
            <span className="rail-count-label">threads</span>
          </div>
          <button
            className="rail-icon-btn"
            onClick={startConv}
            title="New conversation"
          >+</button>
        </aside>
      ) : (
        <aside className="conv-sidebar">
          <button
            className="rail-toggle rail-toggle-inline"
            onClick={toggleThreads}
            title="Collapse threads"
          >‹</button>
          <div className="conv-toolbar">
            <button className="btn-primary small full" onClick={startConv}>
              + New conversation
            </button>
            <button
              className="btn-secondary small full"
              onClick={() => setShowMemory(true)}
              style={{ marginTop: 6 }}
            >
              Memory
            </button>
            <button
              className="btn-secondary small full"
              onClick={() => setShowRubrics(true)}
              style={{ marginTop: 6 }}
            >
              Manage rubrics
            </button>
          </div>
          <ul className="conv-list">
            {list.length === 0 && <li className="empty">No conversations yet.</li>}
            {list.map((c) => (
              <li key={c.id} className={`conv-item ${active?.id === c.id ? "active" : ""}`}>
                <button className="conv-item-button" onClick={() => openConv(c.id)}>
                  <div className="conv-title">{c.title}</div>
                  <div className="conv-meta">
                    {c.turn_count} turn{c.turn_count !== 1 ? "s" : ""}
                    {c.pin_count > 0 && ` · ${c.pin_count} pinned`}
                  </div>
                </button>
                <button className="conv-delete" onClick={() => deleteConv(c.id)}>×</button>
              </li>
            ))}
          </ul>
        </aside>
      )}

      <main className="conv-main">
        {!active ? (
          <div className="empty-state">
            <h2>Pick a conversation or start a new one</h2>
            <p>Each conversation is a research thread your team can revisit. The LLM uses the knowledge graph as one data source and falls back to general knowledge when the graph is silent.</p>
            <p>Set an <strong>Intent</strong> to shape the answer style (e.g. propose-strategy vs. pressure-test) and pick a <strong>Rubric</strong> to apply Appfire-specific framing rules to every answer.</p>
          </div>
        ) : (
          <>
            <header className="conv-header">
              <div className="conv-header-left">
                {titleDraft === null ? (
                  <h2
                    className="conv-title-editable"
                    title="Click to rename"
                    onClick={() => setTitleDraft(active.title)}
                  >
                    {active.title}
                  </h2>
                ) : (
                  <input
                    className="conv-title-input"
                    autoFocus
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onBlur={commitTitle}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        commitTitle();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        setTitleDraft(null);
                      }
                    }}
                  />
                )}
                <ConversationSettingsControl
                  intents={intents}
                  intentGroups={intentGroups}
                  rubrics={rubrics}
                  models={models}
                  defaultModel={defaultModel}
                  intent={active.intent ?? null}
                  rubricId={active.rubric_id ?? null}
                  inferenceStrategy={active.inference_strategy ?? "none"}
                  webGrounding={!!active.web_grounding}
                  autoMemory={!!active.auto_memory}
                  answerModel={active.answer_model ?? null}
                  onChange={updateSettings}
                />
              </div>
              <div className="conv-header-right">
                <span className="muted-note">{active.turns.length} turns · {active.pins.length} pinned</span>
                <button className="btn-secondary small" onClick={doExport} disabled={active.turns.length === 0}>
                  Export report
                </button>
                <button
                  className="btn-secondary small"
                  onClick={sendReportToArtifact}
                  disabled={active.turns.length === 0 || savingReport}
                  title="Run the same exec-Markdown export, save it as a Conversation report artifact, and link it back to this thread for re-sync"
                >
                  {savingReport ? <><span className="spinner" /> Saving…</> : "📎 Save report as artifact"}
                </button>
                <button
                  className="btn-secondary small"
                  onClick={onToggleWideMode}
                  title={wideMode ? "Switch to reading mode (~78 char width)" : "Use full width for prose"}
                >
                  {wideMode ? "📖 Reading" : "↔ Wide"}
                </button>
                <button
                  className={`btn-secondary small ${hideDiagnostics ? "active" : ""}`}
                  onClick={toggleDiagnostics}
                  title={
                    hideDiagnostics
                      ? "Show grounding badges and diagnostic traces"
                      : "Hide grounding badges and diagnostic traces — prose only"
                  }
                >
                  {hideDiagnostics ? "👁 Show diagnostics" : "🅷 Hide diagnostics"}
                </button>
              </div>
            </header>

            <div className="conv-turns">
              {active.turns.length === 0 && (
                <div className="empty-state">
                  <h2>Ask the first question</h2>
                  <p>Multi-turn — follow-ups remember earlier context.</p>
                </div>
              )}
              {active.turns.map((t, i) => {
                const priorUser = (() => {
                  for (let j = i - 1; j >= 0; j--) {
                    const pt = active.turns[j];
                    if (pt.role === "user") return pt.text;
                  }
                  return "";
                })();
                return (
                  <div key={i} id={`turn-${active.id}-${i}`}>
                    <TurnView
                      turn={t}
                      turnIdx={i}
                      onNodeClick={onNodeClick}
                      onPinAnswer={() => t.role === "assistant" && pinAnswer(t)}
                      onPinNode={pinNode}
                      onSaveAsKBFact={onSaveAsKBFact}
                      onSendToArtifact={
                        t.role === "assistant"
                          ? () => sendTurnToArtifact(t, priorUser)
                          : undefined
                      }
                      onExplainInfluence={
                        t.role === "assistant" ? () => setInfluenceTurnIdx(i) : undefined
                      }
                    />
                  </div>
                );
              })}
              {sending && (
                <div className="turn turn-assistant">
                  <div className="turn-label">Assistant</div>
                  <div className="turn-body"><span className="spinner" /> Routing through the graph…</div>
                </div>
              )}
              <div ref={turnsEndRef} />
            </div>

            <form
              className="conv-composer"
              onSubmit={(e) => { e.preventDefault(); send(); }}
            >
              <textarea
                placeholder={
                  activeRubric
                    ? `Ask anything — answers framed by "${activeRubric.name}"`
                    : "Ask anything — graph + general knowledge"
                }
                value={composer}
                onChange={(e) => setComposer(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    send();
                  }
                }}
                disabled={sending}
                rows={3}
              />
              <div className="composer-row">
                <span className="muted-note">⌘/Ctrl+Enter to send</span>
                {onAdvancedSimulate && (
                  <button
                    type="button"
                    className="btn-secondary small"
                    onClick={() => onAdvancedSimulate(
                      composer.trim() || active?.title || "",
                      active?.id,
                      active?.title,
                    )}
                    disabled={sending}
                    title="Open ForeSight for a multi-round debate with configurable personas — scenarios are synthesized from this conversation"
                    style={{ marginLeft: "auto" }}
                  >
                    🔮 Simulate
                  </button>
                )}
                <button className="btn-primary" disabled={sending || !composer.trim()}>
                  {sending ? "Sending…" : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </main>

      {showPinsRail && (
        pinsCollapsed ? (
          <aside className="conv-pins conv-pins-collapsed">
            <button className="rail-toggle" onClick={togglePins} title="Expand outline & pins">‹</button>
            <div className="rail-meta">
              <span className="rail-count">{userTurns.length}</span>
              <span className="rail-count-label">questions</span>
            </div>
            {hasPins && (
              <div className="rail-meta">
                <span className="rail-count">{active!.pins.length}</span>
                <span className="rail-count-label">pinned</span>
              </div>
            )}
          </aside>
        ) : (
          <aside className="conv-pins">
            <button
              className="rail-toggle rail-toggle-inline"
              onClick={togglePins}
              title="Collapse outline & pins"
            >›</button>
            {hasQuestions && (
              <div className="conv-outline">
                <div className="conv-outline-head">
                  <span>Outline</span>
                  <span>{userTurns.length} {userTurns.length === 1 ? "question" : "questions"}</span>
                </div>
                <ol className="conv-outline-list">
                  {userTurns.map(({ turn, idx }, n) => (
                    <li key={idx} className="conv-outline-item">
                      <span className="conv-outline-num">{n + 1}</span>
                      <button
                        className={`conv-outline-link ${activeTurnIdx === idx ? "active" : ""}`}
                        onClick={() => jumpToTurn(idx)}
                        title={turn.role === "user" ? turn.text : ""}
                      >
                        {turn.role === "user" ? turn.text : ""}
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {hasPins && (
              <>
                <h3>Pinned in this thread</h3>
                <ul>
                  {active!.pins.map((p) => (
                    <li key={p.id} className={`pin pin-${p.kind}`}>
                      <span className="pin-kind">{p.kind}</span>
                      {p.kind === "node" && p.label && (
                        <button className="link-btn" onClick={() => onNodeClick(p.label!)}>{p.label}</button>
                      )}
                      {p.kind === "answer" && p.text && <span className="pin-text">{p.text}</span>}
                      {p.kind === "note" && p.text && <span className="pin-text">{p.text}</span>}
                      <button className="conv-delete small" onClick={() => unpin(p)}>×</button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </aside>
        )
      )}

      {showRubrics && <RubricManager open onClose={() => { setShowRubrics(false); reloadIntentsRubrics(); }} />}
      {showMemory && <MemoryDrawer open onClose={() => setShowMemory(false)} />}
      {active && influenceTurnIdx != null && (
        <InfluenceDrawer
          open
          target={{ kind: "turn", conversationId: active.id, turnIdx: influenceTurnIdx }}
          onClose={() => setInfluenceTurnIdx(null)}
          onApplyLever={applyInfluenceLever}
        />
      )}
      {exportOpen && (
        <ExportModal
          markdown={exportText}
          onClose={() => setExportOpen(false)}
          onDownload={downloadExport}
        />
      )}

      {error && <div className="toast error" style={{ position: "fixed", bottom: 24, right: 24 }}>{error}</div>}
    </div>
  );
}

function ConversationSettingsControl({
  intents,
  intentGroups,
  rubrics,
  models,
  defaultModel,
  intent,
  rubricId,
  inferenceStrategy,
  webGrounding,
  autoMemory,
  answerModel,
  onChange,
}: {
  intents: Record<string, string>;
  intentGroups: IntentGroup[];
  rubrics: Rubric[];
  models: ModelOption[];
  defaultModel: string;
  intent: string | null;
  rubricId: string | null;
  inferenceStrategy: string;
  webGrounding: boolean;
  autoMemory: boolean;
  answerModel: string | null;
  onChange: (settings: {
    intent?: string | null;
    rubric_id?: string | null;
    inference_strategy?: string;
    web_grounding?: boolean;
    auto_memory?: boolean;
    answer_model?: string | null;
  }) => void;
}) {
  return (
    <div className="conv-settings">
      <label className="settings-pill">
        <span>Intent</span>
        <IntentSelect
          value={intent}
          onChange={(next) => onChange({ intent: next })}
          groups={intentGroups}
          flatLabels={intents}
          placeholder="— none —"
        />
      </label>
      <label className="settings-pill">
        <span>Rubric</span>
        <select
          value={rubricId ?? ""}
          onChange={(e) => onChange({ rubric_id: e.target.value || null })}
        >
          <option value="">— none —</option>
          {rubrics.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
      </label>
      <label className="settings-pill">
        <span>Model</span>
        <select
          value={answerModel ?? ""}
          onChange={(e) => onChange({ answer_model: e.target.value || null })}
          title="Synthesizer model. Sonnet 4.6 is balanced; Opus for highest quality; Haiku for speed/cost."
        >
          <option value="">default ({modelLabel(models, defaultModel)})</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.label}{m.hint ? ` — ${m.hint}` : ""}</option>
          ))}
        </select>
      </label>
      <label className="settings-pill">
        <span>Inference</span>
        <select
          value={inferenceStrategy}
          onChange={(e) => onChange({ inference_strategy: e.target.value })}
          title="Reflection / CoVe / Best-of-3 add cost but improve quality"
        >
          {Object.entries(INFERENCE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </label>
      <label className="settings-pill checkbox">
        <input
          type="checkbox"
          checked={webGrounding}
          onChange={(e) => onChange({ web_grounding: e.target.checked })}
        />
        <span>Web grounding</span>
      </label>
      <label className="settings-pill checkbox">
        <input
          type="checkbox"
          checked={autoMemory}
          onChange={(e) => onChange({ auto_memory: e.target.checked })}
        />
        <span>Auto-memory</span>
      </label>
    </div>
  );
}

function ExportModal({
  markdown,
  onClose,
  onDownload,
}: {
  markdown: string | null;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Executive report</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>
        <div className="modal-body">
          {markdown == null ? (
            <div className="empty-state"><div className="spinner" /> Drafting report…</div>
          ) : (
            <pre className="export-preview">{markdown}</pre>
          )}
        </div>
        <footer className="modal-footer">
          <button
            className="btn-secondary"
            disabled={!markdown}
            onClick={() => {
              if (!markdown) return;
              navigator.clipboard.writeText(markdown);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? "Copied!" : "Copy markdown"}
          </button>
          <button className="btn-primary" disabled={!markdown} onClick={onDownload}>
            Download .md
          </button>
        </footer>
      </div>
    </div>
  );
}

function TurnView({
  turn,
  turnIdx: _turnIdx,
  onNodeClick,
  onPinAnswer,
  onPinNode,
  onSaveAsKBFact,
  onSendToArtifact,
  onExplainInfluence,
}: {
  turn: Turn;
  turnIdx: number;
  onNodeClick: (label: string) => void;
  onPinAnswer: () => void;
  onPinNode: (nodeId: string, label: string) => void;
  onSaveAsKBFact?: (prefill: RefinePrefill) => void;
  onSendToArtifact?: () => void | Promise<void>;
  onExplainInfluence?: () => void;
}) {
  if (turn.role === "user") {
    return (
      <div className="turn turn-user">
        <div className="turn-label">You</div>
        <div className="turn-body">{turn.text}</div>
      </div>
    );
  }
  if (turn.role === "simulation") {
    return (
      <div className="turn turn-simulation">
        <SimulationView simulation={turn.simulation} />
      </div>
    );
  }
  const a = turn;
  const hasWeb = a.web_sources && a.web_sources.length > 0;
  const strategy = a.inference_strategy && a.inference_strategy !== "none" ? a.inference_strategy : null;
  return (
    <div className="turn turn-assistant">
      <div className="turn-label">
        Assistant
        {a.grounded ? (
          <span className="grounding-badge grounded">graph-grounded</span>
        ) : a.needs_graph === false ? (
          <span className="grounding-badge general">general knowledge</span>
        ) : (
          <span className="grounding-badge nograph">graph silent</span>
        )}
        {hasWeb && <span className="grounding-badge web" title="Verified against live web">web-verified</span>}
        {strategy && (
          <span className="grounding-badge strategy" title="Multi-step inference">{INFERENCE_LABELS[strategy] ?? strategy}</span>
        )}
        {a.memory_used && (
          <span className="grounding-badge memory" title="Persistent memory was injected">memory</span>
        )}
        <button className="link-btn small" onClick={onPinAnswer}>+ pin answer</button>
        {onExplainInfluence && (
          <button
            className="link-btn small"
            title="Show what shaped this answer + what to change to get a different one"
            onClick={onExplainInfluence}
          >
            🔍 Why?
          </button>
        )}
        {onSendToArtifact && (
          <button
            className="link-btn small"
            title="Save this answer as a durable Artifact (Conversation note)"
            onClick={() => { void onSendToArtifact(); }}
          >
            📎 send to artifact
          </button>
        )}
        {onSaveAsKBFact && (
          <button
            className="link-btn small"
            title="Capture a correction / addition tied to this answer's entry nodes"
            onClick={() => {
              const firstNodeId = a.entry_node_ids?.[0];
              const firstNodeLabel = a.entry_node_labels?.[0];
              onSaveAsKBFact({
                kind: "correction",
                target_node_id: firstNodeId ?? null,
                target_node_label: firstNodeLabel ?? null,
                original_summary: firstNodeLabel ?? "",
                new_summary: "",
                reason: a.text.slice(0, 200),
                source_type: "human",
              });
            }}
          >
            💡 save as KB fact
          </button>
        )}
      </div>
      <MarkdownView className="turn-body">{a.text}</MarkdownView>
      {a.gaps && a.gaps.length > 0 && (
        <div className="answer-gaps" aria-label="What the brain doesn't know yet">
          <div className="answer-gaps-label">⚠ What the brain doesn't know yet</div>
          <ul>
            {a.gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}
      {hasWeb && (
        <details className="turn-trace">
          <summary>{a.web_sources!.length} web source{a.web_sources!.length !== 1 ? "s" : ""} consulted</summary>
          <ul>
            {a.web_sources!.map((s, i) => (
              <li key={i}>
                <a href={s.url} target="_blank" rel="noreferrer" className="link-btn">
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
      {a.inference_steps && a.inference_steps.length > 1 && (
        <details className="turn-trace">
          <summary>{a.inference_steps.length} inference steps</summary>
          <ul>
            {a.inference_steps.map((s, i) => (
              <li key={i}>
                <span>{s.label}</span>
                {s.tokens && (
                  <span className="muted-note" style={{ marginLeft: 6 }}>
                    {(s.tokens.input ?? 0).toLocaleString()} in / {(s.tokens.output ?? 0).toLocaleString()} out
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {a.entry_node_labels && a.entry_node_labels.length > 0 && (
        <details className="turn-trace">
          <summary>
            Routed through {a.entry_node_labels.length} entry node{a.entry_node_labels.length !== 1 ? "s" : ""}
            {a.router_reasoning ? ` · ${a.router_reasoning}` : ""}
          </summary>
          <ul>
            {a.entry_node_ids?.map((nid, i) => {
              const label = a.entry_node_labels?.[i] ?? nid;
              return (
                <li key={nid}>
                  <button className="link-btn" onClick={() => onNodeClick(label)}>{label}</button>
                  <button className="link-btn small" onClick={() => onPinNode(nid, label)}>pin</button>
                </li>
              );
            })}
          </ul>
          {a.subgraph_node_count != null && (
            <div className="muted-note">Subgraph: {a.subgraph_node_count} nodes traversed.</div>
          )}
        </details>
      )}
    </div>
  );
}
