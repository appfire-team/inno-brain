import { useEffect, useState } from "react";
import { api, type Rubric } from "../api";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function RubricManager({ open, onClose }: Props) {
  const [rubrics, setRubrics] = useState<Rubric[]>([]);
  const [selected, setSelected] = useState<Rubric | null>(null);
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    try {
      const r = await api.rubrics();
      setRubrics(r.rubrics);
      if (selected) {
        const fresh = r.rubrics.find((x) => x.id === selected.id);
        if (fresh) setSelected(fresh);
        return;
      }
      // Auto-select Appfire Context on first open so users land on the
      // built-in framing instead of a blank "new rubric" editor.
      const appfire =
        r.rubrics.find((x) => x.name === "Appfire Context") ??
        r.rubrics.find((x) => x.source === "builtin" || x.source === "customized") ??
        null;
      if (appfire) {
        setSelected(appfire);
        setName(appfire.name);
        setBody(appfire.body);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    if (open) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const startNew = () => {
    setSelected(null);
    setName("");
    setBody("");
  };

  const editExisting = (r: Rubric) => {
    setSelected(r);
    setName(r.name);
    setBody(r.body);
  };

  const save = async () => {
    if (!name.trim() || !body.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (selected) {
        await api.updateRubric(selected.id, name.trim(), body);
      } else {
        await api.createRubric(name.trim(), body);
        setName("");
        setBody("");
      }
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (r: Rubric) => {
    if (r.source === "builtin") return; // can't happen via UI; defensive
    const msg =
      r.source === "customized"
        ? "Discard your customizations and restore the built-in default?"
        : "Delete this rubric?";
    if (!confirm(msg)) return;
    try {
      if (r.source === "customized") {
        await api.restoreDefaultRubric(r.id);
      } else {
        await api.deleteRubric(r.id);
      }
      if (selected?.id === r.id) startNew();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restoreDefault = async () => {
    if (!selected || selected.source !== "customized") return;
    if (!confirm("Discard your customizations and restore the built-in default?")) return;
    try {
      const restored = await api.restoreDefaultRubric(selected.id);
      setSelected(restored);
      setName(restored.name);
      setBody(restored.body);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return null;

  const isBuiltinSelected = selected?.source === "builtin";
  const isCustomizedSelected = selected?.source === "customized";

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer rubric-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Rubrics</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>
        <div className="rubric-body">
          <aside className="rubric-list">
            <button className="btn-secondary small full" onClick={startNew}>+ New rubric</button>
            <ul>
              {rubrics.map((r) => (
                <li
                  key={r.id}
                  className={selected?.id === r.id ? "active" : ""}
                >
                  <button className="rubric-item-button" onClick={() => editExisting(r)}>
                    {r.name}
                    {r.source === "builtin" && <span className="src-badge src-builtin">built-in</span>}
                    {r.source === "customized" && <span className="src-badge src-customized">customized</span>}
                  </button>
                  {r.source !== "builtin" && (
                    <button
                      className="conv-delete"
                      title={r.source === "customized" ? "Restore default" : "Delete rubric"}
                      onClick={() => remove(r)}
                    >×</button>
                  )}
                </li>
              ))}
              {rubrics.length === 0 && <li className="empty">No rubrics yet.</li>}
            </ul>
          </aside>
          <div className="rubric-editor">
            {(isBuiltinSelected || isCustomizedSelected) && (
              <div className="builtin-banner">
                {isBuiltinSelected
                  ? "This is a built-in rubric. Editing it will save your changes as an override; the built-in body stays in code so you can restore it any time."
                  : "You've customized this built-in. Click Restore default to revert to the original."}
              </div>
            )}
            <input
              className="text-input"
              placeholder="Rubric name (e.g. Appfire framing)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <textarea
              className="rubric-body-input"
              placeholder="Rules / framing the LLM should apply to every turn that uses this rubric…"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={18}
            />
            <div className="rubric-actions">
              <span className="muted-note">
                {selected ? `Editing ${selected.name}` : "New rubric"}
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                {isCustomizedSelected && (
                  <button className="btn-secondary" onClick={restoreDefault}>
                    Restore default
                  </button>
                )}
                <button
                  className="btn-primary"
                  onClick={save}
                  disabled={saving || !name.trim() || !body.trim()}
                >
                  {saving
                    ? "Saving…"
                    : isBuiltinSelected
                      ? "Save as override"
                      : selected
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
