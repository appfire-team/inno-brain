import { useEffect, useState } from "react";
import { api, type ForesightPersona } from "../api";

type Props = {
  open: boolean;
  onClose: () => void;
  onChange: () => void; // tells parent to reload personas
};

const DEFAULT_COLORS = [
  "#818cf8", "#34d399", "#fb7185", "#fbbf24", "#22d3ee",
  "#a78bfa", "#f472b6", "#fb923c", "#60a5fa", "#94a3b8",
];

export function PersonaLibraryDrawer({ open, onClose, onChange }: Props) {
  const [personas, setPersonas] = useState<ForesightPersona[]>([]);
  const [editing, setEditing] = useState<ForesightPersona | null>(null);
  const [label, setLabel] = useState("");
  const [tagline, setTagline] = useState("");
  const [system, setSystem] = useState("");
  const [color, setColor] = useState(DEFAULT_COLORS[0]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    try {
      const r = await api.foresightPersonas();
      setPersonas(r.personas);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    if (open) reload();
  }, [open]);

  const startNew = () => {
    setEditing(null);
    setLabel("");
    setTagline("");
    setSystem("");
    setColor(DEFAULT_COLORS[Math.floor(Math.random() * DEFAULT_COLORS.length)]);
  };

  const edit = (p: ForesightPersona) => {
    setEditing(p);
    setLabel(p.label);
    setTagline(p.tagline ?? "");
    setSystem(p.system);
    setColor(p.color ?? DEFAULT_COLORS[0]);
  };

  const save = async () => {
    if (!label.trim() || !system.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (editing && (editing.source === "custom" || editing.source === "preset" || editing.source === "customized")) {
        // For preset ids this materializes / patches the override file.
        await api.foresightUpdatePersona(editing.id, {
          label: label.trim(), tagline: tagline.trim(), system, color,
        });
      } else {
        await api.foresightCreatePersona({
          label: label.trim(), tagline: tagline.trim(), system, color,
        });
        startNew();
      }
      await reload();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (p: ForesightPersona) => {
    if (p.source === "preset") return; // preset with no override — nothing to delete
    const msg =
      p.source === "customized"
        ? `Discard your customizations to "${p.label}" and restore the preset?`
        : `Delete persona "${p.label}"?`;
    if (!confirm(msg)) return;
    try {
      await api.foresightDeletePersona(p.id);
      if (editing?.id === p.id) startNew();
      await reload();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restoreDefault = async () => {
    if (!editing || editing.source !== "customized") return;
    if (!confirm("Discard your customizations and restore the preset?")) return;
    try {
      const restored = await api.foresightRestorePreset(editing.id);
      setEditing(restored);
      setLabel(restored.label);
      setTagline(restored.tagline ?? "");
      setSystem(restored.system);
      setColor(restored.color ?? DEFAULT_COLORS[0]);
      await reload();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer rubric-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Persona library</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>
        <div className="rubric-body">
          <aside className="rubric-list">
            <button className="btn-secondary small full" onClick={startNew}>+ Custom persona</button>
            <div className="muted-note" style={{ marginTop: 6, fontSize: 10 }}>PRESETS (editable)</div>
            <ul>
              {personas.filter((p) => p.source === "preset" || p.source === "customized").map((p) => (
                <li key={p.id} className={editing?.id === p.id ? "active" : ""}>
                  <button className="rubric-item-button" onClick={() => edit(p)}>
                    <span className="legend-swatch" style={{ background: p.color ?? "var(--accent)" }} />
                    {p.label}
                    {p.source === "customized" && <span className="src-badge src-customized">customized</span>}
                    {p.source === "preset" && <span className="src-badge src-builtin">preset</span>}
                  </button>
                  {p.source === "customized" && (
                    <button className="conv-delete" title="Restore preset" onClick={() => remove(p)}>×</button>
                  )}
                </li>
              ))}
            </ul>
            <div className="muted-note" style={{ marginTop: 10, fontSize: 10 }}>CUSTOM</div>
            <ul>
              {personas.filter((p) => p.source === "custom").map((p) => (
                <li key={p.id} className={editing?.id === p.id ? "active" : ""}>
                  <button className="rubric-item-button" onClick={() => edit(p)}>
                    <span className="legend-swatch" style={{ background: p.color ?? "var(--accent)" }} />
                    {p.label}
                  </button>
                  <button className="conv-delete" onClick={() => remove(p)}>×</button>
                </li>
              ))}
              {personas.filter((p) => p.source === "custom").length === 0 && (
                <li className="empty">None yet.</li>
              )}
            </ul>
          </aside>
          <div className="rubric-editor">
            {(editing?.source === "preset" || editing?.source === "customized") && (
              <div className="builtin-banner">
                {editing.source === "preset"
                  ? "This is a preset. Editing it will save your changes as an override; the original definition stays in code so you can restore it any time."
                  : "You've customized this preset. Click Restore preset to revert to the original."}
              </div>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="text-input"
                style={{ flex: 1 }}
                placeholder="Name (e.g. 'Skeptical Board Member')"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                title="Color"
                style={{ width: 36, height: 36, padding: 2, border: "1px solid var(--border)", borderRadius: 6, background: "transparent" }}
              />
            </div>
            <input
              className="text-input"
              placeholder="Tagline (e.g. 'churn-first, paranoid about renewals')"
              value={tagline}
              onChange={(e) => setTagline(e.target.value)}
            />
            <textarea
              className="rubric-body-input"
              placeholder="System prompt for this persona. Define their viewpoint, what they care about, and how they argue."
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              rows={14}
            />
            <div className="rubric-actions">
              <span className="muted-note">
                {editing?.source === "preset"
                  ? `Editing preset: ${editing.label} (will create an override)`
                  : editing?.source === "customized"
                    ? `Editing customized preset: ${editing.label}`
                    : editing
                      ? `Editing custom: ${editing.label}`
                      : "New custom persona"}
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                {editing?.source === "customized" && (
                  <button className="btn-secondary" onClick={restoreDefault}>Restore preset</button>
                )}
                <button
                  className="btn-primary"
                  disabled={saving || !label.trim() || !system.trim()}
                  onClick={save}
                >
                  {saving
                    ? "Saving…"
                    : editing?.source === "preset"
                      ? "Save as override"
                      : editing
                        ? "Update"
                        : "Create"}
                </button>
              </div>
            </div>
            {error && <div className="error-text">{error}</div>}
          </div>
        </div>
      </aside>
    </div>
  );
}
