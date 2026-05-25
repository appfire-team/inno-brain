import { useEffect, useRef, useState } from "react";
import {
  api,
  getActiveWorkspaceId,
  setActiveWorkspaceId,
  type AvailableRubric,
  type WorkspaceSummary,
} from "../api";

type Props = {
  active: WorkspaceSummary | null;
  onChanged: () => void;
  onNotify?: (kind: "success" | "error" | "info", message: string) => void;
  // A tick that callers can bump to programmatically open the "Create
  // workspace" form — used by the onboarding checklist's first-step CTA.
  openCreateSignal?: number;
};

export function WorkspaceSwitcher({ active, onChanged, onNotify, openCreateSignal }: Props) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<WorkspaceSummary[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [cloneSourceId, setCloneSourceId] = useState<string>("");
  // Rubric picker for the new workspace. Empty string means "no seed rubric".
  // The value encodes both the source workspace and the rubric id as
  // `${workspace_id || ''}::${rubric_id}` so a single <select> can mix
  // built-ins (workspace_id="") with workspace-stored rubrics.
  const [rubricPick, setRubricPick] = useState<string>("");
  const [availableRubrics, setAvailableRubrics] = useState<AvailableRubric[]>([]);
  const [busy, setBusy] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    api
      .workspaces()
      .then((r) => setList(r.workspaces))
      .catch((e) => onNotify?.("error", (e as Error).message));
  }, [open, onNotify]);

  // Close dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // External "open create form" signal — onboarding checklist nudges this.
  useEffect(() => {
    if (!openCreateSignal) return;
    setOpen(true);
    setShowCreate(true);
  }, [openCreateSignal]);

  // Load the rubric picker options whenever the create form opens. The
  // /available endpoint is workspace-independent so it doesn't depend on the
  // current X-Workspace-Id.
  useEffect(() => {
    if (!showCreate) return;
    api
      .availableRubrics()
      .then((r) => setAvailableRubrics(r.rubrics))
      .catch((e) => onNotify?.("error", (e as Error).message));
  }, [showCreate, onNotify]);

  const switchTo = (id: string) => {
    if (id === getActiveWorkspaceId()) {
      setOpen(false);
      return;
    }
    setActiveWorkspaceId(id);
    setOpen(false);
    onChanged();
  };

  const handleCreate = async () => {
    if (!createName.trim()) return;
    setBusy(true);
    try {
      const src = cloneSourceId || null;
      // Parse the picker value back into a seed_rubrics entry. Empty string
      // means no seed; otherwise `${workspace_id || ''}::${rubric_id}`.
      let seedRubrics: Array<{ workspace_id: string | null; rubric_id: string }> | null = null;
      if (rubricPick) {
        const [wsPart, ridPart] = rubricPick.split("::");
        if (ridPart) {
          seedRubrics = [{ workspace_id: wsPart || null, rubric_id: ridPart }];
        }
      }
      const ws = await api.createWorkspace(createName.trim(), src, seedRubrics);
      setActiveWorkspaceId(ws.id);
      setCreateName("");
      setCloneSourceId("");
      setRubricPick("");
      setShowCreate(false);
      setOpen(false);
      const sourceName = src ? list.find((w) => w.id === src)?.name ?? src : null;
      onNotify?.("success", sourceName ? `Cloned from ${sourceName} → ${ws.name}` : `Created ${ws.name}`);
      onChanged();
    } catch (e) {
      onNotify?.("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleRename = async (ws: WorkspaceSummary) => {
    const name = prompt("Rename workspace", ws.name);
    if (!name || name.trim() === ws.name) return;
    try {
      await api.renameWorkspace(ws.id, name.trim());
      const refreshed = await api.workspaces();
      setList(refreshed.workspaces);
      onChanged();
    } catch (e) {
      onNotify?.("error", (e as Error).message);
    }
  };

  const handleDelete = async (ws: WorkspaceSummary) => {
    if (!confirm(`Delete workspace "${ws.name}" and all its data? This cannot be undone.`)) return;
    try {
      await api.deleteWorkspace(ws.id);
      if (getActiveWorkspaceId() === ws.id) {
        const refreshed = await api.workspaces();
        const next = refreshed.workspaces[0];
        if (next) setActiveWorkspaceId(next.id);
        setList(refreshed.workspaces);
      } else {
        const refreshed = await api.workspaces();
        setList(refreshed.workspaces);
      }
      onNotify?.("success", `Deleted ${ws.name}`);
      onChanged();
    } catch (e) {
      onNotify?.("error", (e as Error).message);
    }
  };

  return (
    <div className="workspace-switcher" ref={containerRef}>
      <button
        className="ws-button"
        onClick={() => setOpen((v) => !v)}
        title="Active workspace"
      >
        <span className="ws-icon" aria-hidden>▣</span>
        <span className="ws-name">{active?.name ?? "(no workspace)"}</span>
        <span className="ws-caret">▾</span>
      </button>
      {open && (
        <div className="ws-menu" role="menu">
          <div className="ws-menu-head">Workspaces</div>
          <ul className="ws-list">
            {list.map((ws) => {
              const isActive = ws.id === active?.id;
              return (
                <li key={ws.id} className={isActive ? "active" : ""}>
                  <button className="ws-item" onClick={() => switchTo(ws.id)}>
                    <div className="ws-item-name">
                      {ws.name}
                      {isActive && <span className="ws-active-dot">●</span>}
                    </div>
                    <div className="ws-item-meta">
                      {(() => {
                        // Show repos chip only when non-zero so workspaces
                        // with just docs stay compact. A repo-only workspace
                        // would otherwise read "0 docs" — misleading.
                        const parts: string[] = [];
                        if (ws.stats.repos && ws.stats.repos > 0) parts.push(`${ws.stats.repos} repos`);
                        parts.push(`${ws.stats.documents} docs`);
                        parts.push(`${ws.stats.conversations} convos`);
                        parts.push(`${ws.stats.foresight_sessions} foresight`);
                        return parts.join(" · ");
                      })()}
                    </div>
                  </button>
                  <div className="ws-item-actions">
                    <button title="Rename" onClick={() => handleRename(ws)}>✎</button>
                    {list.length > 1 && (
                      <button title="Delete" onClick={() => handleDelete(ws)}>✕</button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {!showCreate && (
            <button className="ws-new-btn" onClick={() => setShowCreate(true)}>
              + New workspace
            </button>
          )}
          {showCreate && (
            <div className="ws-create">
              <input
                autoFocus
                placeholder="Workspace name"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                  if (e.key === "Escape") {
                    setShowCreate(false);
                    setCreateName("");
                    setCloneSourceId("");
                  }
                }}
              />
              <label className="ws-clone-field">
                <span>Clone from</span>
                <select
                  value={cloneSourceId}
                  onChange={(e) => setCloneSourceId(e.target.value)}
                  title="Copies the source workspace's docs + graph + insights. Conversations & foresight sessions are NOT copied."
                >
                  <option value="">— empty workspace —</option>
                  {list.map((w) => {
                    const repos = w.stats.repos || 0;
                    const meta = repos > 0
                      ? `${repos} repos, ${w.stats.documents} docs`
                      : `${w.stats.documents} docs`;
                    return (
                      <option key={w.id} value={w.id}>
                        {w.name} ({meta})
                      </option>
                    );
                  })}
                </select>
              </label>
              <label className="ws-clone-field">
                <span>Seed rubric</span>
                <select
                  value={rubricPick}
                  onChange={(e) => setRubricPick(e.target.value)}
                  title="Copy a rubric snapshot into the new workspace. Edits won't propagate back."
                >
                  <option value="">— no rubric —</option>
                  {availableRubrics.map((r) => {
                    const value = `${r.workspace_id ?? ""}::${r.id}`;
                    const where = r.workspace_id ? r.workspace_name ?? r.workspace_id : "built-in";
                    return (
                      <option key={value} value={value}>
                        {r.name} ({where})
                      </option>
                    );
                  })}
                </select>
              </label>
              <div className="ws-create-actions">
                <button
                  className="btn-secondary small"
                  onClick={() => {
                    setShowCreate(false);
                    setCreateName("");
                    setCloneSourceId("");
                    setRubricPick("");
                  }}
                >
                  Cancel
                </button>
                <button
                  className="btn-primary small"
                  disabled={busy || !createName.trim()}
                  onClick={handleCreate}
                >
                  {busy ? <span className="spinner" /> : "Create"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
