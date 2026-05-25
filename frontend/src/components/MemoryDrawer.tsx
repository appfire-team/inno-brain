import { useEffect, useState } from "react";
import { api, type MemoryItem } from "../api";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function MemoryDrawer({ open, onClose }: Props) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    try {
      const r = await api.memory();
      setItems(r.items);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    if (open) reload();
  }, [open]);

  const add = async () => {
    if (!draft.trim()) return;
    setSaving(true);
    try {
      await api.createMemory(draft.trim());
      setDraft("");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (it: MemoryItem) => {
    setEditingId(it.id);
    setEditText(it.text);
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await api.updateMemory(editingId, editText.trim());
      setEditingId(null);
      setEditText("");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this memory item?")) return;
    try {
      await api.deleteMemory(id);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer rubric-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Memory</h2>
          <button className="drawer-close" onClick={onClose}>×</button>
        </header>
        <div className="drawer-body">
          <p className="muted-note">
            Persistent facts about the team and corpus that get folded into every Conversations turn.
            Use this for stable preferences and durable context (e.g. "Our team is evaluating Appfire's 2026 strategy"), not single-use details.
          </p>

          <form
            className="memory-add"
            onSubmit={(e) => { e.preventDefault(); add(); }}
          >
            <textarea
              className="rubric-body-input"
              rows={2}
              placeholder="e.g. The team prioritizes Atlassian-Marketplace-native plays over horizontal SaaS."
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <button className="btn-primary" disabled={saving || !draft.trim()}>
              {saving ? "…" : "+ Add memory"}
            </button>
          </form>

          <section style={{ marginTop: 18 }}>
            <h4>{items.length} item{items.length !== 1 ? "s" : ""}</h4>
            {items.length === 0 ? (
              <div className="empty">No memory yet.</div>
            ) : (
              <ul className="memory-list">
                {items.map((it) => (
                  <li key={it.id}>
                    <span className={`memory-source memory-source-${it.source}`}>{it.source}</span>
                    {editingId === it.id ? (
                      <textarea
                        className="rubric-body-input"
                        rows={2}
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                      />
                    ) : (
                      <span className="memory-text">{it.text}</span>
                    )}
                    <div className="memory-actions">
                      {editingId === it.id ? (
                        <>
                          <button className="link-btn small" onClick={saveEdit}>save</button>
                          <button className="link-btn small" onClick={() => setEditingId(null)}>cancel</button>
                        </>
                      ) : (
                        <>
                          <button className="link-btn small" onClick={() => startEdit(it)}>edit</button>
                          <button className="link-btn small" onClick={() => remove(it.id)}>delete</button>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {error && <div className="error-text" style={{ marginTop: 12 }}>{error}</div>}
        </div>
      </aside>
    </div>
  );
}
