import { useEffect, useState } from "react";
import { api, type CustomIntent, type IntentScope, type IntentSource } from "../api";

type Props = {
  onClose: () => void;
};

type IntentGroupRow = {
  label: string;
  intents: Array<{ id: string; label: string; source: IntentSource; body?: string }>;
};

type EditState = {
  mode: "create" | "edit";
  intent: CustomIntent;
};

export function IntentLibrary({ onClose }: Props) {
  const [groups, setGroups] = useState<IntentGroupRow[]>([]);
  const [customs, setCustoms] = useState<CustomIntent[]>([]);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Cache of fetched intent bodies (built-in or user) keyed by id.
  const [bodyCache, setBodyCache] = useState<Record<string, string>>({});

  const refresh = async () => {
    try {
      const [g, c] = await Promise.all([api.intents(), api.customIntents()]);
      const rows: IntentGroupRow[] = [];
      for (const grp of g.groups ?? []) {
        const items = Array.isArray(grp.intents)
          ? grp.intents
          : Object.entries(grp.intents as Record<string, string>).map(([id, label]) => ({
              id, label, source: "builtin" as IntentSource,
            }));
        rows.push({ label: grp.label, intents: items });
      }
      setGroups(rows);
      setCustoms(c.intents);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => { refresh(); }, []);

  const findCustom = (id: string) => customs.find((c) => c.id === id);

  const toggleExpanded = async (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // Lazy-load body the first time a row is expanded.
    if (!(id in bodyCache)) {
      try {
        const src = await api.getIntentSource(id);
        setBodyCache((prev) => ({ ...prev, [id]: src.body }));
      } catch {
        setBodyCache((prev) => ({ ...prev, [id]: "" }));
      }
    }
  };

  const startCreate = () => {
    setEditing({
      mode: "create",
      intent: {
        id: "", group: "Custom", label: "", body: "",
        scope: "workspace", created_at: 0, updated_at: 0,
      },
    });
  };

  const startEdit = (id: string) => {
    const c = findCustom(id);
    if (!c) return;
    setEditing({ mode: "edit", intent: { ...c } });
  };

  const clone = async (id: string) => {
    try {
      const created = await api.cloneIntent(id, { scope: "workspace" });
      await refresh();
      startEdit(created.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Open the editor for a built-in id with its canonical body pre-filled.
  // The first save against this id materializes an override (PATCH route does
  // the materialization on the backend).
  const editBuiltin = async (id: string, label: string, group: string) => {
    let body = bodyCache[id];
    if (body === undefined) {
      try {
        const src = await api.getIntentSource(id);
        body = src.body;
      } catch {
        body = "";
      }
      setBodyCache((prev) => ({ ...prev, [id]: body! }));
    }
    setEditing({
      mode: "edit",
      intent: {
        id, group, label, body: body!,
        scope: "workspace", created_at: 0, updated_at: 0,
      },
    });
  };

  const restoreDefault = async (id: string) => {
    if (!confirm(`Discard your customizations to "${id}" and restore the built-in default?`)) return;
    try {
      const restored = await api.restoreDefaultIntent(id);
      // Refresh the cached body so the row's expanded view shows the canonical one.
      setBodyCache((prev) => ({ ...prev, [id]: restored.body }));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (id: string) => {
    if (!confirm(`Delete intent "${id}"? This can't be undone.`)) return;
    try {
      await api.deleteIntent(id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const save = async () => {
    if (!editing) return;
    const it = editing.intent;
    try {
      if (editing.mode === "create") {
        await api.createIntent({
          id: it.id.trim(), group: it.group.trim(), label: it.label.trim(),
          body: it.body, scope: it.scope,
        });
      } else {
        await api.updateIntent(it.id, {
          group: it.group, label: it.label, body: it.body,
        });
      }
      setEditing(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (editing) {
    // Decide which banner to show. A built-in row id appears in `groups` with
    // source "builtin" (no override) or "customized" (override exists). User
    // intents with non-builtin ids fall through to neither banner.
    const allRows = groups.flatMap((g) => g.intents);
    const rowForEditing = allRows.find((i) => i.id === editing.intent.id);
    const isFirstOverride = editing.mode === "edit" && rowForEditing?.source === "builtin";
    const isCustomizedEdit = editing.mode === "edit" && rowForEditing?.source === "customized";
    return (
      <div className="library-view">
        <header className="library-head">
          <button className="btn-secondary small" onClick={() => setEditing(null)}>← Back</button>
          <h2>{editing.mode === "create" ? "New intent" : `Edit intent: ${editing.intent.label}`}</h2>
        </header>
        {error && <div className="pb-run-error">{error}</div>}
        {(isFirstOverride || isCustomizedEdit) && (
          <div className="builtin-banner">
            {isFirstOverride
              ? "You're about to override the built-in. Saving creates a workspace-scoped override; the canonical body stays in code so you can restore it any time."
              : "You're editing your override of a built-in. Use Restore default to revert."}
          </div>
        )}
        <div className="library-form">
          <label className="modal-field">
            <span>ID <small className="muted-note">(letters, digits, _ — used by playbook steps)</small></span>
            <input
              type="text"
              value={editing.intent.id}
              disabled={editing.mode === "edit"}
              onChange={(e) => setEditing({ ...editing, intent: { ...editing.intent, id: e.target.value.replace(/[^a-zA-Z0-9_-]/g, "_") } })}
              placeholder="pm_competitive_audit"
            />
          </label>
          <label className="modal-field">
            <span>Group</span>
            <input
              type="text"
              value={editing.intent.group}
              onChange={(e) => setEditing({ ...editing, intent: { ...editing.intent, group: e.target.value } })}
              placeholder="Product Manager"
            />
          </label>
          <label className="modal-field">
            <span>Label</span>
            <input
              type="text"
              value={editing.intent.label}
              onChange={(e) => setEditing({ ...editing, intent: { ...editing.intent, label: e.target.value } })}
              placeholder="Audit the competitive landscape"
            />
          </label>
          <label className="modal-field">
            <span>Scope</span>
            <select
              value={editing.intent.scope}
              disabled={editing.mode === "edit"}
              onChange={(e) => setEditing({ ...editing, intent: { ...editing.intent, scope: e.target.value as IntentScope } })}
            >
              <option value="workspace">Workspace (this workspace only)</option>
              <option value="global">Global (all workspaces)</option>
            </select>
          </label>
          <label className="modal-field">
            <span>Instruction body</span>
            <textarea
              rows={10}
              value={editing.intent.body}
              onChange={(e) => setEditing({ ...editing, intent: { ...editing.intent, body: e.target.value } })}
              placeholder="One paragraph telling the model what to produce when this intent is selected."
            />
          </label>
          <div className="library-actions">
            <button className="btn-secondary small" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary small" onClick={save}>Save</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="library-view">
      <header className="library-head">
        <button className="btn-secondary small" onClick={onClose}>← Back</button>
        <h2>Intent library</h2>
        <button className="btn-primary small" onClick={startCreate} style={{ marginLeft: "auto" }}>
          + New intent
        </button>
      </header>
      {error && <div className="pb-run-error">{error}</div>}
      <p className="muted-note">
        Intents are the one-paragraph instructions that shape what each playbook step produces.
        Click <strong>Edit</strong> on a built-in to override it in place — the canonical body stays
        in code so you can restore the default any time. <strong>Duplicate</strong> instead if you
        want a separately-named copy that doesn't change what the built-in id means.
      </p>
      <div className="library-list">
        {groups.map((g) => (
          <section key={g.label} className="library-group">
            <h3>{g.label}</h3>
            <ul>
              {g.intents.map((i) => {
                const isOpen = expanded.has(i.id);
                const custom = i.source !== "builtin" ? findCustom(i.id) : null;
                return (
                  <li key={i.id} className={`library-item library-source-${i.source}`}>
                    <button
                      className="library-row"
                      onClick={() => toggleExpanded(i.id)}
                    >
                      <span className={`library-source-badge library-source-badge-${i.source}`}>
                        {i.source}
                      </span>
                      <span className="library-id"><code>{i.id}</code></span>
                      <span className="library-label">{i.label}</span>
                    </button>
                    {isOpen && (
                      <div className="library-detail">
                        {(() => {
                          const body = custom?.body ?? bodyCache[i.id];
                          if (body === undefined) {
                            return <div className="muted-note">Loading…</div>;
                          }
                          if (!body) {
                            return <div className="muted-note">(empty body)</div>;
                          }
                          return <pre className="library-body">{body}</pre>;
                        })()}
                        <div className="library-actions">
                          {i.source === "builtin" && (
                            <>
                              <button className="btn-primary small" onClick={() => editBuiltin(i.id, i.label, g.label)}>
                                Edit (override)
                              </button>
                              <button className="btn-secondary small" onClick={() => clone(i.id)}>
                                Duplicate to new id
                              </button>
                            </>
                          )}
                          {i.source === "customized" && (
                            <>
                              <button className="btn-primary small" onClick={() => startEdit(i.id)}>
                                Edit
                              </button>
                              <button className="btn-secondary small" onClick={() => restoreDefault(i.id)}>
                                Restore default
                              </button>
                              <button className="btn-secondary small" onClick={() => clone(i.id)}>
                                Duplicate to new id
                              </button>
                            </>
                          )}
                          {(i.source === "workspace" || i.source === "global") && (
                            <>
                              <button className="btn-secondary small" onClick={() => startEdit(i.id)}>
                                Edit
                              </button>
                              <button className="btn-secondary small" onClick={() => remove(i.id)}>
                                Delete
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
