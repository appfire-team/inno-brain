import { useEffect, useMemo, useState } from "react";
import {
  api, type ForesightPersona, type IntentScope, type PlaybookSpec, type PlaybookStepSpec,
  type PlaybookTemplate,
} from "../api";

type Props = {
  onClose: () => void;
  templates: PlaybookTemplate[];
  artifactTypes: Record<string, string>;
  stepTypes: string[];
  onAfterChange: () => void;
};

type EditState = {
  mode: "create" | "edit";
  spec: PlaybookSpec;
  originalId?: string; // for edits — the id used in the PATCH URL
};

export function PlaybookBuilder({ onClose, templates, artifactTypes, stepTypes, onAfterChange }: Props) {
  const [editing, setEditing] = useState<EditState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [intentLabels, setIntentLabels] = useState<Record<string, string>>({});
  const [foresightPersonas, setForesightPersonas] = useState<ForesightPersona[]>([]);
  const [simulatePersonas, setSimulatePersonas] = useState<Array<{ key: string; label: string }>>([]);
  const [horizons, setHorizons] = useState<Record<string, string>>({});

  useEffect(() => {
    Promise.all([
      api.intents(),
      api.foresightPersonas().catch(() => ({ personas: [] as ForesightPersona[] })),
      api.simulatePersonas().catch(() => ({ personas: [], horizons: {} })),
    ]).then(([ints, fp, sp]) => {
      setIntentLabels(ints.intents);
      setForesightPersonas(fp.personas);
      setSimulatePersonas(sp.personas);
      setHorizons(sp.horizons);
    });
  }, []);

  const blankStep = (type: string): PlaybookStepSpec => {
    const base: PlaybookStepSpec = { id: `step_${Date.now().toString(36).slice(-4)}`, label: "", type: type as PlaybookStepSpec["type"] };
    if (type === "intent_turn") base.intent = "";
    if (type === "foresight") { base.personas = []; base.rounds = 1; }
    if (type === "simulate") base.personas = [];
    if (type === "synthesize") base.sections = [];
    return base;
  };

  const startCreate = () => {
    setEditing({
      mode: "create",
      spec: {
        id: "", label: "", tagline: "", expected_duration_s: 240,
        accepts_source_types: [], artifact_type: "StrategyBrief",
        steps: [
          { id: "step_1", label: "First step", type: "intent_turn", intent: "" },
          { id: "synth", label: "Compose brief", type: "synthesize", sections: ["Recommendation"] },
        ],
        scope: "workspace",
      },
    });
  };

  const startEdit = async (id: string) => {
    try {
      const spec = await api.getPlaybookSpec(id);
      setEditing({ mode: "edit", originalId: id, spec });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const clone = async (id: string) => {
    try {
      const created = await api.clonePlaybook(id, { scope: "workspace" });
      onAfterChange();
      startEdit(created.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (t: PlaybookTemplate) => {
    if (t.source === "builtin") return;
    const msg = t.source === "customized"
      ? `Discard your customizations to "${t.label}" and restore the built-in default?`
      : `Delete playbook "${t.id}"? This can't be undone.`;
    if (!confirm(msg)) return;
    try {
      if (t.source === "customized") {
        await api.restoreDefaultPlaybook(t.id);
      } else {
        await api.deleteCustomPlaybook(t.id);
      }
      onAfterChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restoreDefault = async (id: string) => {
    if (!confirm("Discard your customizations and restore the built-in default?")) return;
    try {
      await api.restoreDefaultPlaybook(id);
      onAfterChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const save = async () => {
    if (!editing) return;
    const s = editing.spec;
    try {
      if (editing.mode === "create") {
        await api.createCustomPlaybook(s);
      } else if (editing.originalId) {
        // PATCH route materializes an override automatically when originalId
        // matches a built-in id.
        await api.updateCustomPlaybook(editing.originalId, s);
      }
      setEditing(null);
      onAfterChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (editing) {
    return (
      <BuilderForm
        editing={editing}
        setEditing={setEditing}
        onCancel={() => setEditing(null)}
        onSave={save}
        error={error}
        intentLabels={intentLabels}
        foresightPersonas={foresightPersonas}
        simulatePersonas={simulatePersonas}
        horizons={horizons}
        artifactTypes={artifactTypes}
        stepTypes={stepTypes}
        blankStep={blankStep}
      />
    );
  }

  return (
    <div className="library-view">
      <header className="library-head">
        <button className="btn-secondary small" onClick={onClose}>← Back</button>
        <h2>Playbook builder</h2>
        <button className="btn-primary small" onClick={startCreate} style={{ marginLeft: "auto" }}>
          + New playbook
        </button>
      </header>
      {error && <div className="pb-run-error">{error}</div>}
      <p className="muted-note">
        Compose a custom playbook by chaining intents, foresight debates, simulations, fact-check,
        and a final synthesis. Click <strong>Edit</strong> on a built-in to override it in place;
        the canonical body stays in code so you can restore the default any time. Use
        <strong> Duplicate</strong> to make a separately-named copy.
      </p>
      <ul className="library-list-flat">
        {templates.map((t) => {
          const src = t.source ?? "builtin";
          return (
            <li key={t.id} className={`library-item library-source-${src}`}>
              <div className="library-row">
                <span className={`library-source-badge library-source-badge-${src}`}>
                  {src}
                </span>
                <span className="library-id"><code>{t.id}</code></span>
                <span className="library-label">{t.label}</span>
                <span className="muted-note">{t.steps.length} steps · {artifactTypes[t.artifact_type] ?? t.artifact_type}</span>
                <div className="library-actions" style={{ marginLeft: "auto" }}>
                  {src === "builtin" && (
                    <>
                      <button className="btn-primary small" onClick={() => startEdit(t.id)}>Edit (override)</button>
                      <button className="btn-secondary small" onClick={() => clone(t.id)}>Duplicate</button>
                    </>
                  )}
                  {src === "customized" && (
                    <>
                      <button className="btn-primary small" onClick={() => startEdit(t.id)}>Edit</button>
                      <button className="btn-secondary small" onClick={() => restoreDefault(t.id)}>Restore default</button>
                      <button className="btn-secondary small" onClick={() => clone(t.id)}>Duplicate</button>
                    </>
                  )}
                  {(src === "workspace" || src === "global") && (
                    <>
                      <button className="btn-secondary small" onClick={() => startEdit(t.id)}>Edit</button>
                      <button className="btn-secondary small" onClick={() => remove(t)}>Delete</button>
                    </>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ----- Builder form -----

function BuilderForm({
  editing, setEditing, onCancel, onSave, error,
  intentLabels, foresightPersonas, simulatePersonas, horizons,
  artifactTypes, stepTypes, blankStep,
}: {
  editing: EditState;
  setEditing: (e: EditState) => void;
  onCancel: () => void;
  onSave: () => void;
  error: string | null;
  intentLabels: Record<string, string>;
  foresightPersonas: ForesightPersona[];
  simulatePersonas: Array<{ key: string; label: string }>;
  horizons: Record<string, string>;
  artifactTypes: Record<string, string>;
  stepTypes: string[];
  blankStep: (type: string) => PlaybookStepSpec;
}) {
  const s = editing.spec;
  const updateSpec = (patch: Partial<PlaybookSpec>) => setEditing({ ...editing, spec: { ...s, ...patch } });
  const updateStep = (idx: number, patch: Partial<PlaybookStepSpec>) => {
    const next = [...s.steps];
    next[idx] = { ...next[idx], ...patch };
    updateSpec({ steps: next });
  };
  const moveStep = (idx: number, delta: number) => {
    const target = idx + delta;
    if (target < 0 || target >= s.steps.length) return;
    const next = [...s.steps];
    [next[idx], next[target]] = [next[target], next[idx]];
    updateSpec({ steps: next });
  };
  const addStep = (type: string) => {
    // New steps land immediately before the synth (last) step.
    const next = [...s.steps];
    const insertAt = next.length > 0 && next[next.length - 1].type === "synthesize"
      ? next.length - 1 : next.length;
    next.splice(insertAt, 0, blankStep(type));
    updateSpec({ steps: next });
  };
  const removeStep = (idx: number) => {
    const next = [...s.steps];
    next.splice(idx, 1);
    updateSpec({ steps: next });
  };

  const isBuiltinOverride = editing.mode === "edit" && (s.source === "builtin" || s.source === "customized");
  return (
    <div className="library-view">
      <header className="library-head">
        <button className="btn-secondary small" onClick={onCancel}>← Back</button>
        <h2>{editing.mode === "create" ? "New playbook" : `Edit: ${s.label || s.id}`}</h2>
        <button className="btn-primary small" onClick={onSave} style={{ marginLeft: "auto" }}>
          {editing.mode === "create"
            ? "Create"
            : s.source === "builtin"
              ? "Save as override"
              : "Save changes"}
        </button>
      </header>
      {error && <div className="pb-run-error">{error}</div>}
      {isBuiltinOverride && (
        <div className="builtin-banner">
          {s.source === "builtin"
            ? "You're about to override a built-in playbook. Saving creates a workspace-scoped override; the canonical version stays in code so you can restore it any time."
            : "You're editing your override of a built-in. Use Restore default from the list to revert."}
        </div>
      )}

      <div className="library-form">
        <div className="row-grid">
          <label className="modal-field">
            <span>ID <small className="muted-note">(letters, digits, _)</small></span>
            <input
              type="text"
              value={s.id}
              disabled={editing.mode === "edit"}
              onChange={(e) => updateSpec({ id: e.target.value.replace(/[^a-zA-Z0-9_-]/g, "_") })}
              placeholder="my_custom_playbook"
            />
          </label>
          <label className="modal-field">
            <span>Scope</span>
            <select
              value={s.scope ?? "workspace"}
              disabled={editing.mode === "edit"}
              onChange={(e) => updateSpec({ scope: e.target.value as IntentScope })}
            >
              <option value="workspace">Workspace</option>
              <option value="global">Global</option>
            </select>
          </label>
        </div>
        <label className="modal-field">
          <span>Label</span>
          <input
            type="text" value={s.label}
            onChange={(e) => updateSpec({ label: e.target.value })}
            placeholder="What does this playbook do?"
          />
        </label>
        <label className="modal-field">
          <span>Tagline</span>
          <input
            type="text" value={s.tagline}
            onChange={(e) => updateSpec({ tagline: e.target.value })}
            placeholder="One-sentence summary shown in the picker."
          />
        </label>
        <div className="row-grid">
          <label className="modal-field">
            <span>Output artifact type</span>
            <select
              value={s.artifact_type}
              onChange={(e) => updateSpec({ artifact_type: e.target.value })}
            >
              {Object.entries(artifactTypes).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          <label className="modal-field">
            <span>Expected duration (s)</span>
            <input
              type="number"
              value={s.expected_duration_s}
              onChange={(e) => updateSpec({ expected_duration_s: parseInt(e.target.value || "0", 10) || 0 })}
            />
          </label>
        </div>
        <label className="modal-field">
          <span>Accepts source artifact types</span>
          <div className="checkbox-grid">
            {Object.entries(artifactTypes).map(([k, v]) => (
              <label key={k}>
                <input
                  type="checkbox"
                  checked={s.accepts_source_types.includes(k)}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...s.accepts_source_types, k]
                      : s.accepts_source_types.filter((t) => t !== k);
                    updateSpec({ accepts_source_types: next });
                  }}
                />
                {v}
              </label>
            ))}
          </div>
          <small className="muted-note">Which prior artifacts can this playbook build on?</small>
        </label>

        <h3 style={{ marginTop: 24 }}>Steps</h3>
        <ol className="builder-steps">
          {s.steps.map((step, idx) => (
            <li key={idx} className="builder-step">
              <div className="builder-step-head">
                <span className="builder-step-num">{idx + 1}</span>
                <select
                  value={step.type}
                  onChange={(e) => updateStep(idx, { type: e.target.value as PlaybookStepSpec["type"] })}
                >
                  {stepTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <input
                  type="text"
                  value={step.id}
                  onChange={(e) => updateStep(idx, { id: e.target.value.replace(/[^a-z0-9_]/g, "_").toLowerCase() })}
                  placeholder="step_id"
                  style={{ width: 140 }}
                />
                <input
                  type="text"
                  value={step.label}
                  onChange={(e) => updateStep(idx, { label: e.target.value })}
                  placeholder="Step label"
                  style={{ flex: 1 }}
                />
                <button className="btn-secondary small" onClick={() => moveStep(idx, -1)} disabled={idx === 0}>↑</button>
                <button className="btn-secondary small" onClick={() => moveStep(idx, 1)} disabled={idx === s.steps.length - 1}>↓</button>
                <button className="btn-secondary small" onClick={() => removeStep(idx)}>✕</button>
              </div>
              <StepFields
                step={step}
                onChange={(patch) => updateStep(idx, patch)}
                intentLabels={intentLabels}
                foresightPersonas={foresightPersonas}
                simulatePersonas={simulatePersonas}
                horizons={horizons}
              />
            </li>
          ))}
        </ol>
        <div className="builder-add-step">
          <span className="muted-note">Add step:</span>
          {stepTypes.filter((t) => t !== "synthesize").map((t) => (
            <button key={t} className="btn-secondary small" onClick={() => addStep(t)}>+ {t}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

function StepFields({
  step, onChange, intentLabels, foresightPersonas, simulatePersonas, horizons,
}: {
  step: PlaybookStepSpec;
  onChange: (patch: Partial<PlaybookStepSpec>) => void;
  intentLabels: Record<string, string>;
  foresightPersonas: ForesightPersona[];
  simulatePersonas: Array<{ key: string; label: string }>;
  horizons: Record<string, string>;
}) {
  const intentOptions = useMemo(() =>
    Object.entries(intentLabels).sort((a, b) => a[1].localeCompare(b[1])),
    [intentLabels]
  );

  if (step.type === "intent_turn") {
    return (
      <div className="builder-step-body">
        <label className="modal-field">
          <span>Intent</span>
          <select
            value={step.intent ?? ""}
            onChange={(e) => onChange({ intent: e.target.value })}
          >
            <option value="">— pick an intent —</option>
            {intentOptions.map(([id, label]) => (
              <option key={id} value={id}>{label} ({id})</option>
            ))}
          </select>
        </label>
      </div>
    );
  }

  if (step.type === "foresight") {
    const selected = step.personas ?? [];
    return (
      <div className="builder-step-body">
        <label className="modal-field">
          <span>Personas</span>
          <div className="checkbox-grid">
            {foresightPersonas.map((p) => (
              <label key={p.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(p.id)}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...selected, p.id]
                      : selected.filter((x) => x !== p.id);
                    onChange({ personas: next });
                  }}
                />
                {p.label}
              </label>
            ))}
          </div>
        </label>
        <label className="modal-field">
          <span>Rounds</span>
          <input
            type="number" min={1} max={5}
            value={step.rounds ?? 1}
            onChange={(e) => onChange({ rounds: parseInt(e.target.value || "1", 10) })}
          />
        </label>
      </div>
    );
  }

  if (step.type === "simulate") {
    const selected = step.personas ?? [];
    return (
      <div className="builder-step-body">
        <label className="modal-field">
          <span>Personas</span>
          <div className="checkbox-grid">
            {simulatePersonas.map((p) => (
              <label key={p.key}>
                <input
                  type="checkbox"
                  checked={selected.includes(p.key)}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...selected, p.key]
                      : selected.filter((x) => x !== p.key);
                    onChange({ personas: next });
                  }}
                />
                {p.label}
              </label>
            ))}
          </div>
        </label>
        <label className="modal-field">
          <span>Horizon (optional)</span>
          <select value={step.horizon ?? ""} onChange={(e) => onChange({ horizon: e.target.value || undefined })}>
            <option value="">— inherit run horizon —</option>
            {Object.entries(horizons).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
      </div>
    );
  }

  if (step.type === "factcheck") {
    return (
      <div className="builder-step-body muted-note">
        Fact-check uses the prior step outputs and verifies load-bearing claims via web search.
        No additional configuration.
      </div>
    );
  }

  if (step.type === "synthesize") {
    const sections = step.sections ?? [];
    return (
      <div className="builder-step-body">
        <label className="modal-field">
          <span>Sections (in order)</span>
          <div className="section-list">
            {sections.map((sec, i) => (
              <div key={i} className="section-row">
                <input
                  type="text"
                  value={sec}
                  onChange={(e) => {
                    const next = [...sections];
                    next[i] = e.target.value;
                    onChange({ sections: next });
                  }}
                  placeholder={`Section ${i + 1}`}
                />
                <button className="btn-secondary small" onClick={() => {
                  if (i === 0) return;
                  const next = [...sections];
                  [next[i - 1], next[i]] = [next[i], next[i - 1]];
                  onChange({ sections: next });
                }}>↑</button>
                <button className="btn-secondary small" onClick={() => {
                  if (i === sections.length - 1) return;
                  const next = [...sections];
                  [next[i + 1], next[i]] = [next[i], next[i + 1]];
                  onChange({ sections: next });
                }}>↓</button>
                <button className="btn-secondary small" onClick={() => {
                  const next = sections.filter((_, k) => k !== i);
                  onChange({ sections: next });
                }}>✕</button>
              </div>
            ))}
          </div>
          <button className="btn-secondary small" onClick={() => onChange({ sections: [...sections, ""] })}>
            + Add section
          </button>
        </label>
      </div>
    );
  }

  return null;
}
