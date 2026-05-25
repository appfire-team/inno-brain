import { useCallback, useEffect, useState } from "react";
import {
  api,
  type ArtifactSummary,
  type ModelOption,
  type PlaybookRun,
  type PlaybookRunSummary,
  type PlaybookTemplate,
  type Rubric,
} from "../api";
import { PlaybookRunView } from "./PlaybookRunView";
import { IntentLibrary } from "./IntentLibrary";
import { PlaybookBuilder } from "./PlaybookBuilder";

type SynthStrategy = "none" | "reflection" | "cove" | "best_of_3";

type KickoffPrefill = {
  scenario: string;
  horizon: string;
  source_artifact_id: string | null;
  rubric_id: string | null;
  web_grounding: boolean;
  synth_inference_strategy: SynthStrategy;
  fact_check: boolean;
  answer_model: string | null;
};

type Props = {
  onBusyChange?: (busy: boolean) => void;
};

const PLAYBOOK_ICONS: Record<string, string> = {
  discover_opportunity: "🧭",
  pressure_test_strategy: "⚖️",
  build_buy_partner: "🔀",
  draft_prd: "📝",
  plan_launch: "🚀",
  codebase_health: "🩺",
};

const ARTIFACT_ICONS: Record<string, string> = {
  OpportunityScan: "🧭",
  StrategyBrief: "⚖️",
  BuildBuyDecision: "🔀",
  PRDDraft: "📝",
  LaunchPlan: "🚀",
  CodebaseAudit: "🩺",
};

export function PlaybooksPanel({ onBusyChange }: Props) {
  const [templates, setTemplates] = useState<PlaybookTemplate[]>([]);
  const [artifactTypes, setArtifactTypes] = useState<Record<string, string>>({});
  const [horizons, setHorizons] = useState<Record<string, string>>({});
  const [synthStrategies, setSynthStrategies] = useState<SynthStrategy[]>(["none", "reflection", "cove", "best_of_3"]);
  const [rubrics, setRubrics] = useState<Rubric[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>("");
  const [runs, setRuns] = useState<PlaybookRunSummary[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [stepTypes, setStepTypes] = useState<string[]>([]);
  const [active, setActive] = useState<
    | { kind: "run"; id: string }
    | { kind: "kickoff"; template: PlaybookTemplate; prefill?: KickoffPrefill }
    | { kind: "intent-library" }
    | { kind: "playbook-builder" }
    | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [t, r, a, rb, mm] = await Promise.all([
        api.playbooks(),
        api.playbookRuns(),
        api.artifacts(),
        api.rubrics().catch(() => ({ rubrics: [] as Rubric[] })),
        api.models().catch(() => ({ models: [] as ModelOption[], default: "" })),
      ]);
      setTemplates(t.playbooks);
      setArtifactTypes(t.artifact_types);
      setHorizons(t.horizons || {});
      setStepTypes((t as any).step_types || ["intent_turn", "foresight", "simulate", "factcheck", "synthesize"]);
      if (t.synth_inference_strategies && t.synth_inference_strategies.length) {
        setSynthStrategies(t.synth_inference_strategies);
      }
      setRuns(r.runs);
      setArtifacts(a.artifacts);
      setRubrics(rb.rubrics);
      setModels(mm.models);
      setDefaultModel(mm.default);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Reflect any in-flight (running/queued) run as "busy" so the tab badge
  // shows a spinner even when the user is elsewhere.
  useEffect(() => {
    const busy = runs.some((r) => r.status === "running" || r.status === "queued");
    onBusyChange?.(busy);
  }, [runs, onBusyChange]);

  // While any run is in-flight, poll for updates.
  useEffect(() => {
    const inFlight = runs.some((r) => r.status === "running" || r.status === "queued");
    if (!inFlight) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [runs, refresh]);

  const handleRerun = (run: PlaybookRun) => {
    const template = templates.find((t) => t.id === run.playbook_id);
    if (!template) return;
    setActive({
      kind: "kickoff",
      template,
      prefill: {
        scenario: run.user_inputs.scenario,
        horizon: run.user_inputs.horizon,
        source_artifact_id: run.user_inputs.source_artifact_id,
        rubric_id: run.user_inputs.rubric_id,
        web_grounding: run.user_inputs.web_grounding,
        synth_inference_strategy: (run.user_inputs.synth_inference_strategy ?? "none") as SynthStrategy,
        fact_check: run.user_inputs.fact_check ?? false,
        answer_model: run.user_inputs.answer_model ?? null,
      },
    });
  };

  if (active?.kind === "run") {
    return (
      <PlaybookRunView
        runId={active.id}
        onClose={() => { setActive(null); refresh(); }}
        onRerun={handleRerun}
        artifactTypes={artifactTypes}
      />
    );
  }

  if (active?.kind === "intent-library") {
    return <IntentLibrary onClose={() => setActive(null)} />;
  }

  if (active?.kind === "playbook-builder") {
    return (
      <PlaybookBuilder
        onClose={() => setActive(null)}
        templates={templates}
        artifactTypes={artifactTypes}
        stepTypes={stepTypes}
        onAfterChange={refresh}
      />
    );
  }

  if (active?.kind === "kickoff") {
    return (
      <PlaybookKickoff
        template={active.template}
        horizons={horizons}
        synthStrategies={synthStrategies}
        rubrics={rubrics}
        models={models}
        defaultModel={defaultModel}
        artifacts={artifacts.filter((a) => active.template.accepts_source_types.includes(a.type))}
        prefill={active.prefill}
        onCancel={() => setActive(null)}
        onLaunched={(runId) => { setActive({ kind: "run", id: runId }); refresh(); }}
      />
    );
  }

  return (
    <div className="pb-panel">
      <header className="pb-head">
        <h2>Playbooks</h2>
        <div className="pb-head-actions">
          <button className="btn-secondary small" onClick={() => setActive({ kind: "intent-library" })}>
            Intents
          </button>
          <button className="btn-secondary small" onClick={() => setActive({ kind: "playbook-builder" })}>
            + New playbook
          </button>
        </div>
        <p className="muted-note">
          End-to-end workflows. Pick one, type a scenario, and get a typed brief out the other side.
          Later playbooks can build on earlier artifacts.
        </p>
      </header>

      <section className="pb-section">
        <h3>Start a playbook</h3>
        <div className="pb-grid">
          {templates.map((t) => {
            const icon = PLAYBOOK_ICONS[t.id] ?? "▣";
            const dur = `~${Math.round(t.expected_duration_s / 60)} min`;
            return (
              <button key={t.id} className="pb-card" onClick={() => setActive({ kind: "kickoff", template: t })}>
                <div className="pb-card-head">
                  <span className="pb-icon">{icon}</span>
                  <span className="pb-card-title">{t.label}</span>
                  {t.source && t.source !== "builtin" && (
                    <span className={`library-source-badge library-source-badge-${t.source}`} style={{ marginLeft: "auto" }}>
                      {t.source}
                    </span>
                  )}
                </div>
                <div className="pb-tagline">{t.tagline}</div>
                <div className="pb-card-meta">
                  <span>{t.steps.length} steps · {dur}</span>
                  <span>↳ {ARTIFACT_ICONS[t.artifact_type] ?? "🗎"} {artifactTypes[t.artifact_type] ?? t.artifact_type}</span>
                </div>
                {t.accepts_source_types.length > 0 && (
                  <div className="pb-accepts">
                    Builds on: {t.accepts_source_types.map((s) => artifactTypes[s] ?? s).join(", ")}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </section>

      {runs.length > 0 && (
        <section className="pb-section">
          <h3>Recent runs</h3>
          <ul className="pb-run-list">
            {runs.slice(0, 12).map((r) => (
              <li key={r.id} className={`pb-run-item pb-status-${r.status}`}>
                <button className="pb-run-btn" onClick={() => setActive({ kind: "run", id: r.id })}>
                  <span className="pb-run-status">{statusLabel(r.status)}</span>
                  <span className="pb-run-title">{r.playbook_label}</span>
                  <span className="pb-run-scenario">{r.scenario}</span>
                  <span className="pb-run-progress">
                    {r.status === "running" ? `step ${r.current_step + 1}/${r.step_count}` : `${r.step_count} steps`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {artifacts.length > 0 && (
        <section className="pb-section">
          <h3>Artifacts shelf</h3>
          <ul className="pb-artifact-list">
            {artifacts.slice(0, 16).map((a) => (
              <li key={a.id} className="pb-artifact-item">
                <button className="pb-artifact-btn" onClick={() => a.playbook_run_id && setActive({ kind: "run", id: a.playbook_run_id })}>
                  <span className="pb-artifact-icon">{ARTIFACT_ICONS[a.type] ?? "🗎"}</span>
                  <div className="pb-artifact-body">
                    <div className="pb-artifact-title">{a.title}</div>
                    <div className="pb-artifact-tldr">{a.tldr || "—"}</div>
                    <div className="pb-artifact-type">{artifactTypes[a.type] ?? a.type}</div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && <div className="toast error" style={{ position: "fixed", bottom: 24, right: 24 }}>{error}</div>}
    </div>
  );
}

function statusLabel(status: PlaybookRunSummary["status"]): string {
  switch (status) {
    case "queued": return "queued";
    case "running": return "running";
    case "complete": return "done";
    case "failed": return "failed";
    case "cancelled": return "cancelled";
  }
}

// ---------- Kickoff form ----------

function PlaybookKickoff({
  template, horizons, synthStrategies, rubrics, models, defaultModel,
  artifacts, prefill, onCancel, onLaunched,
}: {
  template: PlaybookTemplate;
  horizons: Record<string, string>;
  synthStrategies: SynthStrategy[];
  rubrics: Rubric[];
  models: ModelOption[];
  defaultModel: string;
  artifacts: ArtifactSummary[];
  prefill?: KickoffPrefill;
  onCancel: () => void;
  onLaunched: (runId: string) => void;
}) {
  const [scenario, setScenario] = useState(prefill?.scenario ?? "");
  const [suggestions, setSuggestions] = useState<Array<{ text: string; kind: "kb" | "wildcard" }> | null>(null);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [horizon, setHorizon] = useState<string>(prefill?.horizon ?? "1y");
  const [sourceId, setSourceId] = useState<string>(prefill?.source_artifact_id ?? "");
  const [rubricId, setRubricId] = useState<string>(prefill?.rubric_id ?? "");
  const [webGrounding, setWebGrounding] = useState(prefill?.web_grounding ?? true);
  const [synthStrategy, setSynthStrategy] = useState<SynthStrategy>(prefill?.synth_inference_strategy ?? "cove");
  const [factCheck, setFactCheck] = useState<boolean>(prefill?.fact_check ?? true);
  const [answerModel, setAnswerModel] = useState<string>(prefill?.answer_model ?? defaultModel ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Intent labels so the step-plan preview can show each `intent_turn` step's
  // underlying library intent — closes the "I see step X but can't find it in
  // the Intent Library" loop.
  const [intentLabels, setIntentLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    api.intents().then((r) => {
      if (!cancelled) setIntentLabels(r.intents);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Pre-select the workspace's first rubric for a fresh kickoff (no prefill).
  // Execs almost always want the rubric applied; the previous behaviour of
  // silently dropping it was a real bug.
  useEffect(() => {
    if (!prefill && !rubricId && rubrics.length > 0) {
      setRubricId(rubrics[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rubrics]);

  // Fetch corpus-grounded scenario suggestions when the kickoff form opens.
  // Skip when we have a prefill (the user came from a previous run / save-as).
  // The Refresh button calls this with fresh=true to bypass the 15-min cache.
  const fetchSuggestions = useCallback(async (fresh: boolean = true) => {
    setSuggestionsLoading(true);
    try {
      const r = await api.suggestPlaybookScenarios(template.id, fresh);
      setSuggestions(r.scenarios);
    } catch {
      setSuggestions([]);
    } finally {
      setSuggestionsLoading(false);
    }
  }, [template.id]);

  useEffect(() => {
    if (prefill) return;
    let cancelled = false;
    (async () => {
      setSuggestionsLoading(true);
      try {
        const r = await api.suggestPlaybookScenarios(template.id);
        if (!cancelled) setSuggestions(r.scenarios);
      } catch {
        if (!cancelled) setSuggestions([]);
      } finally {
        if (!cancelled) setSuggestionsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [template.id, prefill]);

  const submit = async () => {
    if (!scenario.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await api.runPlaybook({
        playbook_id: template.id,
        scenario: scenario.trim(),
        horizon,
        source_artifact_id: sourceId || null,
        rubric_id: rubricId || null,
        web_grounding: webGrounding,
        synth_inference_strategy: synthStrategy,
        fact_check: factCheck,
        answer_model: answerModel || null,
      });
      onLaunched(run.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="pb-kickoff">
      <header className="pb-kickoff-head">
        <button className="btn-secondary small" onClick={onCancel}>← Back</button>
        <h2>
          <span className="pb-icon">{PLAYBOOK_ICONS[template.id] ?? "▣"}</span>
          {template.label}
        </h2>
        <span className="muted-note">~{Math.round(template.expected_duration_s / 60)} min · {template.steps.length} steps</span>
      </header>
      <p className="muted-note">{template.tagline}</p>

      <div className="pb-kickoff-body">
        <label className="modal-field">
          <span>Scenario or question</span>
          {(suggestionsLoading || (suggestions && suggestions.length > 0)) && (
            <div className="pb-suggestions">
              <div className="pb-suggestions-label">
                <span style={{ flex: 1 }}>
                  {suggestionsLoading ? (
                    <><span className="spinner spinner-inline" /> Reading your KB + company context…</>
                  ) : (
                    <>💡 Suggested scenarios — click to use</>
                  )}
                </span>
                {!suggestionsLoading && suggestions && suggestions.length > 0 && (
                  <button
                    type="button"
                    className="pb-suggestion-refresh"
                    onClick={() => fetchSuggestions(true)}
                    title="Get a fresh set of suggestions (bypasses the 15-min cache)"
                  >
                    ↻ Refresh
                  </button>
                )}
              </div>
              {!suggestionsLoading && suggestions && (
                <div className="pb-suggestion-chips">
                  {suggestions.map((s, i) => (
                    <button
                      key={i}
                      type="button"
                      className={`pb-suggestion-chip pb-suggestion-${s.kind}`}
                      onClick={() => setScenario(s.text)}
                      title={
                        s.kind === "wildcard"
                          ? "Outside your KB but fits the company — a stretch to consider"
                          : "Grounded in entities from your KB"
                      }
                    >
                      <span className={`pb-suggestion-tag pb-suggestion-tag-${s.kind}`}>
                        {s.kind === "wildcard" ? "🌐 wildcard" : "📚 from KB"}
                      </span>
                      <span className="pb-suggestion-text">{s.text}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <textarea
            className="rubric-body-input"
            rows={4}
            placeholder={kickoffPlaceholder(template.id)}
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
            autoFocus
          />
        </label>

        <label className="modal-field">
          <span>Time horizon</span>
          <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
            {Object.entries(horizons).length > 0
              ? Object.entries(horizons).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))
              : (
                <>
                  <option value="3mo">3 months from now</option>
                  <option value="6mo">6 months from now</option>
                  <option value="1y">1 year from now</option>
                  <option value="3y">3 years from now</option>
                  <option value="5y">5 years from now</option>
                </>
              )}
          </select>
          <small className="muted-note">
            Shapes how every step reasons about timing. Watch indicators and ARR estimates anchor to this.
          </small>
        </label>

        {template.accepts_source_types.length > 0 && (
          <label className="modal-field">
            <span>Build on a prior artifact (optional)</span>
            <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
              <option value="">— none —</option>
              {artifacts.map((a) => (
                <option key={a.id} value={a.id}>{a.title} — {a.type}</option>
              ))}
            </select>
            <small className="muted-note">
              The prior artifact's TL;DR + sections are injected as context into the first step.
            </small>
          </label>
        )}

        <label className="modal-field">
          <span>Rubric</span>
          <select value={rubricId} onChange={(e) => setRubricId(e.target.value)}>
            <option value="">— none —</option>
            {rubrics.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
          <small className="muted-note">
            Applied to every step + the synthesizer. Your company-specific framing (capital constraints, Sherlocking risk, etc.) lands here.
          </small>
        </label>

        {models.length > 0 && (
          <label className="modal-field">
            <span>Model</span>
            <select value={answerModel} onChange={(e) => setAnswerModel(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}{m.hint ? ` — ${m.hint}` : ""}
                </option>
              ))}
            </select>
            <small className="muted-note">
              Used for every LLM step in this run (intent turns, foresight, fact-check, synthesis, and later refinements). Simulate keeps its built-in fast/slow split.
            </small>
          </label>
        )}

        <label className="modal-field">
          <span>Synthesizer inference strategy</span>
          <select value={synthStrategy} onChange={(e) => setSynthStrategy(e.target.value as SynthStrategy)}>
            {synthStrategies.map((s) => (
              <option key={s} value={s}>{synthStrategyLabel(s)}</option>
            ))}
          </select>
          <small className="muted-note">
            Wraps only the final SYNTH step. Reflection adds a critique + revise pass (+1 LLM call); CoVe verifies claims; Best-of-3 samples and picks. Earlier steps stay single-pass.
          </small>
        </label>

        <label className="modal-field row">
          <input type="checkbox" checked={factCheck} onChange={(e) => setFactCheck(e.target.checked)} />
          <div>
            <span style={{ display: "block" }}>✅ Fact-check before SYNTH</span>
            <small className="muted-note">
              Inserts a step that extracts load-bearing claims from prior outputs and verifies each via web_search. The brief then hedges on unverified / contradicted claims. Adds ~30-60s.
            </small>
          </div>
        </label>

        <label className="modal-field row">
          <input type="checkbox" checked={webGrounding} onChange={(e) => setWebGrounding(e.target.checked)} />
          <div>
            <span style={{ display: "block" }}>🌐 Web grounding</span>
            <small className="muted-note">Steps may use Anthropic web_search to verify time-sensitive facts.</small>
          </div>
        </label>

        <div className="pb-step-preview">
          <div className="muted-note" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.04 }}>Step plan</div>
          <ol>
            {previewSteps(template.steps, factCheck).map((s, i) => (
              <li key={`${s.id}-${i}`} className={s.injected ? "pb-step-injected" : ""}>
                <span className="pb-step-type">{stepTypeLabel(s.type)}</span>
                <span> {s.label}</span>
                {s.intent && (
                  <span className="pb-step-intent-ref" title={intentLabels[s.intent] ?? s.intent}>
                    {" "}↳ intent <code>{s.intent}</code>
                    {intentLabels[s.intent] ? ` — ${intentLabels[s.intent]}` : ""}
                  </span>
                )}
                {s.injected && <span className="pb-injected-tag"> (added by toggle)</span>}
              </li>
            ))}
          </ol>
          {synthStrategy !== "none" && (
            <small className="muted-note">
              Synth strategy: <strong>{synthStrategyLabel(synthStrategy)}</strong> wraps the final SYNTH step.
            </small>
          )}
        </div>

        {error && <div className="error-text">{error}</div>}

        <div className="pb-kickoff-actions">
          <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button className="btn-primary" disabled={!scenario.trim() || submitting} onClick={submit}>
            {submitting ? <><span className="spinner" /> Launching…</> : "Run playbook"}
          </button>
        </div>
      </div>
    </div>
  );
}

function kickoffPlaceholder(playbookId: string): string {
  switch (playbookId) {
    case "discover_opportunity":
      return "What kind of new product opportunity should we look for? e.g. 'A net-new SaaS bet for Appfire that doesn't risk Atlassian Sherlocking.'";
    case "pressure_test_strategy":
      return "Describe the strategic bet to stress-test. e.g. 'Ship the Opsgenie migration bundle in Q3 2026.'";
    case "build_buy_partner":
      return "What capability are we deciding on? e.g. 'On-call routing engine for our Opsgenie-migration bundle.'";
    case "draft_prd":
      return "What feature or product are we specifying? e.g. 'Cross-repo incident retrospective tooling for SRE teams.'";
    case "plan_launch":
      return "What are we launching? e.g. 'Comala Compliance: FDA QMSR module, Q1 2026.'";
    case "codebase_health":
      return "Any specific focus? e.g. 'Focus on the auth surface and dependency security.' (Or leave broad.)";
    default:
      return "Describe the scenario.";
  }
}

function stepTypeLabel(type: string): string {
  switch (type) {
    case "intent_turn": return "❯ ASK";
    case "foresight": return "⚔ DEBATE";
    case "simulate": return "⚡ SIM";
    case "factcheck": return "✅ FACTCHECK";
    case "synthesize": return "✦ SYNTH";
    default: return type;
  }
}

type PreviewStep = { id: string; label: string; type: string; intent?: string; injected?: boolean };

function previewSteps(steps: PlaybookTemplate["steps"], factCheck: boolean): PreviewStep[] {
  const base: PreviewStep[] = steps.map((s) => ({ id: s.id, label: s.label, type: s.type, intent: s.intent }));
  if (!factCheck) return base;
  // Mirror backend's _resolve_run_steps: splice a factcheck step right before SYNTH.
  const insertAt = Math.max(0, base.length - 1);
  base.splice(insertAt, 0, {
    id: "factcheck",
    label: "Fact-check load-bearing claims",
    type: "factcheck",
    injected: true,
  });
  return base;
}

function synthStrategyLabel(s: SynthStrategy): string {
  switch (s) {
    case "none": return "none (single pass)";
    case "reflection": return "reflection (draft → critique → revise)";
    case "cove": return "chain-of-verification (draft → verify → revise)";
    case "best_of_3": return "best of 3 (sample 3 → pick)";
  }
}
