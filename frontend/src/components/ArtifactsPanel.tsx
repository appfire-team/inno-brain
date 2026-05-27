import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  getActiveWorkspaceId,
  type Artifact,
  type ArtifactComment,
  type ArtifactHighlight,
  type ArtifactSummary,
  type PatchSuggestion,
} from "../api";
import { InlineMarkdown, MarkdownView } from "./MarkdownView";
import { InfluenceDrawer } from "./InfluenceDrawer";

const DOC_LEVEL = "__doc__";

function highlightIcon(tone: string): string {
  switch (tone) {
    case "win": return "✓";
    case "risk": return "⚠";
    case "tension": return "⚡";
    case "number": return "#";
    default: return "•";
  }
}

function relativeTime(ts: number | undefined): string {
  if (!ts) return "";
  const d = Math.max(0, (Date.now() / 1000) - ts);
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

type ToastNotify = (kind: "success" | "error" | "info", msg: string) => void;

type Props = {
  onNotify?: ToastNotify;
  /** Deep-link target. If set on mount, the panel selects this artifact id
   * as soon as the list loads. Used by the App-level URL parser when a user
   * arrives via a "🔗 Copy link" deep link. */
  initialArtifactId?: string | null;
  /** Called once the panel has consumed initialArtifactId so the parent can
   * clear it and avoid re-applying on workspace switches. */
  onInitialArtifactConsumed?: () => void;
};

export function ArtifactsPanel({ onNotify, initialArtifactId, onInitialArtifactConsumed }: Props) {
  const [list, setList] = useState<ArtifactSummary[]>([]);
  const [types, setTypes] = useState<Record<string, string>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [createOpen, setCreateOpen] = useState(false);
  const consumedInitialRef = useRef(false);

  const notify = useCallback<ToastNotify>((kind, msg) => {
    if (onNotify) onNotify(kind, msg);
  }, [onNotify]);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.artifacts();
      setList(r.artifacts);
      setTypes(r.types);
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [notify]);

  const loadArtifact = useCallback(async (id: string) => {
    setViewerLoading(true);
    try {
      const a = await api.getArtifact(id);
      setArtifact(a);
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setViewerLoading(false);
    }
  }, [notify]);

  useEffect(() => { loadList(); }, [loadList]);

  useEffect(() => {
    if (selectedId) loadArtifact(selectedId);
    else setArtifact(null);
  }, [selectedId, loadArtifact]);

  // Group by type for the file-tree look. Newer at top.
  const grouped = useMemo(() => {
    const g: Record<string, ArtifactSummary[]> = {};
    for (const a of list) {
      const k = a.type || "Other";
      (g[k] ||= []).push(a);
    }
    return g;
  }, [list]);

  // Default-expand all groups on first load.
  useEffect(() => {
    if (Object.keys(expandedGroups).length === 0 && Object.keys(grouped).length > 0) {
      const init: Record<string, boolean> = {};
      for (const k of Object.keys(grouped)) init[k] = true;
      setExpandedGroups(init);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grouped]);

  // Deep-link consumption: when initialArtifactId is set AND the list has
  // loaded with that id, select it. One-shot — we then notify the parent so
  // it can clear the prop and not re-apply on workspace switches.
  useEffect(() => {
    if (consumedInitialRef.current) return;
    if (!initialArtifactId) return;
    if (list.length === 0) return;
    const found = list.some((a) => a.id === initialArtifactId);
    if (found) {
      setSelectedId(initialArtifactId);
      consumedInitialRef.current = true;
      onInitialArtifactConsumed?.();
    } else {
      // Artifact id present in URL but not in this workspace's artifacts —
      // notify so the deep link doesn't leave the user on a blank panel forever.
      consumedInitialRef.current = true;
      onInitialArtifactConsumed?.();
      notify("error", "Linked artifact not found in this workspace.");
    }
  }, [initialArtifactId, list, notify, onInitialArtifactConsumed]);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this artifact? This cannot be undone.")) return;
    try {
      await api.deleteArtifact(id);
      if (selectedId === id) setSelectedId(null);
      await loadList();
      notify("success", "Artifact deleted");
    } catch (e) {
      notify("error", (e as Error).message);
    }
  };

  return (
    <div className="art-panel">
      <aside className="art-sidebar">
        <div className="art-sidebar-head">
          <h3>Artifacts</h3>
          <div className="art-sidebar-actions">
            <button className="btn-secondary small" onClick={loadList} title="Reload">↻</button>
            <button
              className="btn-primary small"
              onClick={() => setCreateOpen(true)}
              title="Create a free-form artifact"
            >
              + New
            </button>
          </div>
        </div>
        {loading && <div className="muted-note" style={{ padding: 12 }}>Loading…</div>}
        {!loading && list.length === 0 && (
          <div className="muted-note" style={{ padding: 12 }}>
            No artifacts yet. Run a playbook, or click <strong>+ New</strong>.
          </div>
        )}
        <div className="art-tree">
          {Object.entries(grouped).map(([typeKey, items]) => {
            const label = types[typeKey] ?? typeKey;
            const open = !!expandedGroups[typeKey];
            return (
              <div key={typeKey} className="art-tree-group">
                <button
                  className="art-tree-group-head"
                  onClick={() => setExpandedGroups((s) => ({ ...s, [typeKey]: !open }))}
                  title={`${items.length} item${items.length === 1 ? "" : "s"}`}
                >
                  <span className="art-tree-caret">{open ? "▾" : "▸"}</span>
                  <span className="art-tree-folder" aria-hidden>📁</span>
                  <span className="art-tree-folder-label">{label}</span>
                  <span className="art-tree-count">{items.length}</span>
                </button>
                {open && (
                  <ul className="art-tree-items">
                    {items.map((a) => (
                      <li key={a.id}>
                        <button
                          className={`art-tree-item ${selectedId === a.id ? "active" : ""}`}
                          onClick={() => setSelectedId(a.id)}
                        >
                          <div className="art-tree-item-title">{a.title || "Untitled"}</div>
                          <div className="art-tree-item-meta">
                            <span>{relativeTime(a.updated_at)}</span>
                            {a.source_artifact_ids.length > 0 && (
                              <span className="art-tree-derived" title="Derived from a parent artifact">↳</span>
                            )}
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </aside>

      <section className="art-viewer">
        {!selectedId && (
          <div className="art-empty">
            <div className="art-empty-icon">📄</div>
            <h3>Select an artifact</h3>
            <p className="muted-note">
              Artifacts are the durable outputs of Playbooks, Conversations, and ForeSight.
              Browse them on the left, or use <strong>+ New</strong> to create a free-form note.
            </p>
          </div>
        )}
        {selectedId && viewerLoading && (
          <div className="art-loading"><span className="spinner" /> Loading artifact…</div>
        )}
        {artifact && (
          <ArtifactView
            artifact={artifact}
            allArtifacts={list}
            typeLabel={types[artifact.type] ?? artifact.type}
            onChanged={async () => { await loadList(); await loadArtifact(artifact.id); }}
            onDelete={() => handleDelete(artifact.id)}
            onSelect={(id) => setSelectedId(id)}
            notify={notify}
          />
        )}
      </section>

      {createOpen && (
        <CreateArtifactModal
          types={types}
          onClose={() => setCreateOpen(false)}
          onCreated={async (id) => {
            setCreateOpen(false);
            await loadList();
            setSelectedId(id);
            notify("success", "Artifact created");
          }}
          notify={notify}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Viewer
// ─────────────────────────────────────────────────────────────────────────────

type ViewerProps = {
  artifact: Artifact;
  allArtifacts: ArtifactSummary[];
  typeLabel: string;
  onChanged: () => Promise<void>;
  onDelete: () => void;
  onSelect: (id: string) => void;
  notify: ToastNotify;
};

function ArtifactView({
  artifact, allArtifacts, typeLabel, onChanged, onDelete, onSelect, notify,
}: ViewerProps) {
  const [viewVersion, setViewVersion] = useState<number | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [commentSection, setCommentSection] = useState<string>(DOC_LEVEL);
  const [posting, setPosting] = useState(false);
  const [refining, setRefining] = useState(false);
  const [refineInstruction, setRefineInstruction] = useState("");
  const [refineOpen, setRefineOpen] = useState(false);
  const [refineIncludeQa, setRefineIncludeQa] = useState(false);
  const [refineIncludeConv, setRefineIncludeConv] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [qaQuestion, setQaQuestion] = useState("");
  const [qaBusy, setQaBusy] = useState(false);
  const [simplifying, setSimplifying] = useState(false);
  const [simplifyMode, setSimplifyMode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [patchSuggesting, setPatchSuggesting] = useState<string | null>(null);
  const [patchSuggestion, setPatchSuggestion] = useState<PatchSuggestion | null>(null);
  const [patchApplying, setPatchApplying] = useState(false);
  const [renamingTitle, setRenamingTitle] = useState<string | null>(null);
  const [renameSaving, setRenameSaving] = useState(false);
  const [influenceOpen, setInfluenceOpen] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  const copyDeepLink = useCallback(async () => {
    const ws = getActiveWorkspaceId();
    if (!ws) {
      notify?.("error", "No active workspace — can't build a link.");
      return;
    }
    const params = new URLSearchParams({ ws, tab: "artifacts", artifact: artifact.id });
    const url = `${window.location.origin}/?${params.toString()}`;
    try {
      await navigator.clipboard.writeText(url);
      setLinkCopied(true);
      notify?.("success", "Link copied — paste it to anyone with access to this app.");
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      // Some browsers gate clipboard. Fall back to selecting in a prompt.
      window.prompt("Copy this link:", url);
    }
  }, [artifact.id, notify]);

  const conversationLinkId = (artifact.provenance as { conversation_id?: string } | undefined)?.conversation_id;
  const resyncFromConversation = useCallback(async () => {
    if (!conversationLinkId || resyncing) return;
    setResyncing(true);
    try {
      await api.resyncArtifactFromConversation(artifact.id);
      onChanged();
      notify?.("success", "Re-synced from conversation — added as a new version.");
    } catch (e) {
      notify?.("error", `Re-sync failed: ${(e as Error).message}`);
    } finally {
      setResyncing(false);
    }
  }, [artifact.id, conversationLinkId, resyncing, onChanged, notify]);

  const startRename = () => setRenamingTitle(artifact.title || "");
  const cancelRename = () => setRenamingTitle(null);
  const commitRename = async () => {
    if (renamingTitle == null) return;
    const trimmed = renamingTitle.trim();
    if (!trimmed || trimmed === artifact.title) {
      setRenamingTitle(null);
      return;
    }
    setRenameSaving(true);
    try {
      await api.renameArtifact(artifact.id, trimmed);
      setRenamingTitle(null);
      onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setRenameSaving(false);
    }
  };

  const isOldVersion = viewVersion != null && viewVersion !== artifact.current_version;
  const displayedVersion = useMemo(() => {
    if (viewVersion == null) return artifact.versions[artifact.versions.length - 1];
    return artifact.versions.find((v) => v.v === viewVersion) ?? artifact.versions[artifact.versions.length - 1];
  }, [artifact.versions, viewVersion]);

  const displayedSections = (isOldVersion ? displayedVersion.sections : artifact.sections) || {};
  const displayedTldr = isOldVersion ? displayedVersion.tldr : artifact.tldr;
  const displayedHighlights: ArtifactHighlight[] = (
    isOldVersion ? (displayedVersion.highlights || []) : (artifact.highlights || [])
  );
  const displayedRawMarkdown = isOldVersion ? displayedVersion.raw_markdown : artifact.raw_markdown;

  // Comments
  const openComments = artifact.comments.filter((c) => c.status === "open");
  const addressedComments = artifact.comments.filter((c) => c.status === "addressed");

  const submitComment = async () => {
    const text = commentText.trim();
    if (!text) return;
    setPosting(true);
    try {
      await api.addArtifactComment(artifact.id, {
        text,
        section: commentSection === DOC_LEVEL ? null : commentSection,
      });
      setCommentText("");
      await onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setPosting(false);
    }
  };

  const changeCommentStatus = async (cid: string, status: ArtifactComment["status"]) => {
    try {
      await api.updateArtifactComment(artifact.id, cid, { status });
      await onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    }
  };

  const doRefine = async () => {
    setRefining(true);
    try {
      await api.refineArtifact(artifact.id, {
        instruction: refineInstruction.trim() || undefined,
        include_qa: refineIncludeQa,
        include_conversation: refineIncludeConv,
      });
      setRefineInstruction("");
      setRefineIncludeQa(false);
      setRefineIncludeConv(false);
      setRefineOpen(false);
      await onChanged();
      notify("success", "Refined — new version saved");
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setRefining(false);
    }
  };

  const runSuggestPatch = async (parentId: string) => {
    setPatchSuggesting(parentId);
    setPatchSuggestion(null);
    try {
      const s = await api.suggestPatch(artifact.id, { parent_id: parentId });
      setPatchSuggestion(s);
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setPatchSuggesting(null);
    }
  };

  const applySuggestion = async () => {
    if (!patchSuggestion) return;
    setPatchApplying(true);
    try {
      await api.refineArtifact(artifact.id, { instruction: patchSuggestion.instruction });
      setPatchSuggestion(null);
      await onChanged();
      notify("success", "Patch applied — new version saved");
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setPatchApplying(false);
    }
  };

  const submitQa = async () => {
    const q = qaQuestion.trim();
    if (!q) return;
    setQaBusy(true);
    try {
      await api.askArtifact(artifact.id, q);
      setQaQuestion("");
      await onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setQaBusy(false);
    }
  };

  const toggleSimplify = async () => {
    if (simplifyMode) { setSimplifyMode(false); return; }
    if (artifact.simplified && artifact.simplified.source_updated_at === artifact.updated_at) {
      setSimplifyMode(true);
      return;
    }
    setSimplifying(true);
    try {
      await api.simplifyArtifact(artifact.id, false);
      await onChanged();
      setSimplifyMode(true);
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setSimplifying(false);
    }
  };

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(displayedRawMarkdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      notify("error", "Copy failed");
    }
  };

  const downloadMarkdown = () => {
    const blob = new Blob([displayedRawMarkdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const safeTitle = (artifact.title || "artifact").replace(/[^a-z0-9-_]+/gi, "_");
    a.href = url;
    a.download = `${safeTitle}-v${displayedVersion.v}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Provenance: link back to parent artifacts.
  const parents = useMemo(() => {
    const ids = artifact.provenance?.source_artifact_ids || [];
    return ids.map((id) => allArtifacts.find((a) => a.id === id)).filter(Boolean) as ArtifactSummary[];
  }, [artifact, allArtifacts]);

  // Children: artifacts whose provenance lists this one.
  const children = useMemo(
    () => allArtifacts.filter((a) => (a.source_artifact_ids || []).includes(artifact.id)),
    [allArtifacts, artifact.id],
  );

  return (
    <div className="pb-final-brief">
      <header className="pb-final-brief-head">
        <div className="art-head-left">
          <div className="art-eyebrow">{typeLabel}</div>
          {renamingTitle == null ? (
            <h2 className="art-title-editable">
              <span
                className="art-title-text"
                onDoubleClick={startRename}
                title="Double-click to rename"
              >
                {artifact.title || "Untitled"}
              </span>
              <button
                className="art-title-edit-btn"
                onClick={startRename}
                title="Rename"
                aria-label="Rename"
              >
                ✎
              </button>
            </h2>
          ) : (
            <div className="art-title-rename">
              <input
                autoFocus
                className="text-input"
                value={renamingTitle}
                onChange={(e) => setRenamingTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); void commitRename(); }
                  if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
                }}
                disabled={renameSaving}
              />
              <button className="btn-primary small" onClick={() => void commitRename()} disabled={renameSaving}>
                {renameSaving ? "Saving…" : "Save"}
              </button>
              <button className="btn-secondary small" onClick={cancelRename} disabled={renameSaving}>
                Cancel
              </button>
            </div>
          )}
          {(parents.length > 0 || children.length > 0) && (
            <div className="art-provenance">
              {parents.length > 0 && (
                <div>
                  <span className="muted-note">Derived from:</span>{" "}
                  {parents.map((p) => (
                    <span key={p.id} className="art-parent-chip">
                      <button className="link-btn" onClick={() => onSelect(p.id)}>
                        {p.title || p.id}
                      </button>
                      <button
                        className="link-btn small"
                        onClick={() => runSuggestPatch(p.id)}
                        disabled={!!patchSuggesting}
                        title="Diff this parent's two latest versions and propose a targeted patch to this artifact"
                      >
                        {patchSuggesting === p.id ? <><span className="spinner" /> Diffing…</> : "↻ Suggest patch"}
                      </button>
                    </span>
                  ))}
                </div>
              )}
              {children.length > 0 && (
                <div>
                  <span className="muted-note">Used by:</span>{" "}
                  {children.map((c) => (
                    <button key={c.id} className="link-btn" onClick={() => onSelect(c.id)}>
                      {c.title || c.id}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="pb-artifact-actions">
          {artifact.versions.length > 1 && (
            <select
              className="btn-secondary small"
              value={viewVersion ?? artifact.current_version}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                setViewVersion(v === artifact.current_version ? null : v);
              }}
            >
              {[...artifact.versions].reverse().map((v) => (
                <option key={v.v} value={v.v}>
                  v{v.v}{v.v === artifact.current_version ? " · current" : ""}
                </option>
              ))}
            </select>
          )}
          <button
            className="btn-secondary small"
            onClick={copyDeepLink}
            title="Copy a link to this artifact — paste it to any app user to open this exact view"
          >
            {linkCopied ? "✓ Copied" : "🔗 Copy link"}
          </button>
          {artifact.provenance?.playbook_run_id && (
            <button
              className="btn-secondary small"
              onClick={() => setInfluenceOpen(true)}
              title="Show what shaped this brief — rubric, memory, cross-step convergence"
            >
              🔍 Why?
            </button>
          )}
          {conversationLinkId && (
            <button
              className="btn-secondary small"
              onClick={resyncFromConversation}
              disabled={resyncing}
              title="Re-run the conversation export and add the fresh report as a new version"
            >
              {resyncing ? <><span className="spinner" /> Re-syncing…</> : "↻ Re-sync from conversation"}
            </button>
          )}
          <button
            className="btn-secondary small"
            onClick={() => setReviewOpen((x) => !x)}
            title="Show review comments"
          >
            💬 Comments{openComments.length > 0 && <strong style={{ marginLeft: 4 }}>({openComments.length})</strong>}
          </button>
          <button
            className="btn-secondary small"
            onClick={() => setRefineOpen((x) => !x)}
            disabled={isOldVersion}
            title={isOldVersion ? "Switch to the current version to refine" : "Refine — apply comments + an optional instruction"}
          >
            ↻ Refine
          </button>
          <button
            className={`btn-secondary small ${simplifyMode ? "active" : ""}`}
            onClick={toggleSimplify}
            disabled={simplifying}
          >
            {simplifying ? <><span className="spinner" /> Simplifying…</> : simplifyMode ? "✓ Plain language" : "🧒 Plain language"}
          </button>
          <button className="btn-secondary small" onClick={copyMarkdown}>
            {copied ? "✓ Copied" : "Copy MD"}
          </button>
          <button className="btn-secondary small" onClick={downloadMarkdown}>⇩ .md</button>
          <button className="btn-secondary small art-btn-danger" onClick={onDelete} title="Delete artifact">🗑</button>
        </div>
      </header>

      {isOldVersion && (
        <div className="pb-run-error" style={{ background: "#fef3c7", color: "#92400e", border: "1px solid #fcd34d" }}>
          Viewing <strong>v{viewVersion}</strong> — read-only snapshot.{" "}
          <button className="link-btn" onClick={() => setViewVersion(null)}>
            Jump to current (v{artifact.current_version})
          </button>
        </div>
      )}

      {refineOpen && (() => {
        const qaCount = (artifact.qa_history || []).length;
        const convId = (artifact.provenance as { conversation_id?: string } | undefined)?.conversation_id;
        const hasAny =
          openComments.length > 0 ||
          !!refineInstruction.trim() ||
          (refineIncludeQa && qaCount > 0) ||
          (refineIncludeConv && !!convId);
        return (
          <div className="pb-review-panel art-refine-panel">
            <div className="pb-review-head">
              <strong>Refine</strong>
              <span className="muted-note">
                {openComments.length} open comment{openComments.length === 1 ? "" : "s"} ·
                {qaCount} Q&amp;A · {convId ? "linked conversation" : "no conversation"}
              </span>
            </div>
            <textarea
              className="art-refine-textarea"
              placeholder="(Optional) extra instruction — e.g. sharpen the TL;DR; drop speculation about Q3"
              rows={3}
              value={refineInstruction}
              onChange={(e) => setRefineInstruction(e.target.value)}
            />
            <div className="art-refine-context-row">
              <label className={`art-refine-check ${qaCount === 0 ? "is-disabled" : ""}`}>
                <input
                  type="checkbox"
                  checked={refineIncludeQa}
                  disabled={qaCount === 0}
                  onChange={(e) => setRefineIncludeQa(e.target.checked)}
                />
                <span>Include Q&amp;A ({qaCount})</span>
              </label>
              <label className={`art-refine-check ${!convId ? "is-disabled" : ""}`}>
                <input
                  type="checkbox"
                  checked={refineIncludeConv}
                  disabled={!convId}
                  onChange={(e) => setRefineIncludeConv(e.target.checked)}
                />
                <span>Include source conversation</span>
              </label>
              <div style={{ flex: 1 }} />
              <button
                className="btn-primary small"
                onClick={doRefine}
                disabled={refining || !hasAny}
                title={!hasAny ? "Add a comment, type an instruction, or include Q&A / conversation" : "Creates a new version"}
              >
                {refining ? <><span className="spinner" /> Refining…</> : "↻ Run refine"}
              </button>
            </div>
          </div>
        );
      })()}

      {reviewOpen && (
        <div className="pb-review-panel">
          <div className="pb-review-head">
            <strong>Review</strong>
            <span className="muted-note" style={{ marginLeft: 8 }}>
              {openComments.length} open · {addressedComments.length} addressed · {artifact.comments.length} total
            </span>
          </div>
          <div className="pb-review-composer">
            <select value={commentSection} onChange={(e) => setCommentSection(e.target.value)}>
              <option value={DOC_LEVEL}>Whole document</option>
              {Object.keys(artifact.sections).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <textarea
              placeholder="What should be revised?"
              rows={2}
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
            />
            <button
              className="btn-primary small"
              onClick={submitComment}
              disabled={!commentText.trim() || posting}
            >
              {posting ? <span className="spinner" /> : "Add comment"}
            </button>
          </div>
          {artifact.comments.length === 0 ? (
            <div className="muted-note">No comments yet. Add one above.</div>
          ) : (
            <ul className="pb-comment-list">
              {artifact.comments.map((c) => (
                <li key={c.id} className={`pb-comment pb-comment-${c.status}`}>
                  <div className="pb-comment-meta">
                    <span className={`pb-comment-status pb-comment-status-${c.status}`}>{c.status}</span>
                    <span className="muted-note">
                      on {c.section ? `"${c.section}"` : "whole document"}
                      {c.addressed_in_version != null && ` · addressed in v${c.addressed_in_version}`}
                    </span>
                  </div>
                  <div className="pb-comment-text">{c.text}</div>
                  <div className="pb-comment-actions">
                    {c.status === "open" && (
                      <button className="link-btn" onClick={() => changeCommentStatus(c.id, "resolved")}>
                        Mark resolved
                      </button>
                    )}
                    {c.status === "addressed" && (
                      <>
                        <button className="link-btn" onClick={() => changeCommentStatus(c.id, "resolved")}>
                          Looks good — resolve
                        </button>
                        <button className="link-btn" onClick={() => changeCommentStatus(c.id, "open")}>
                          Reopen
                        </button>
                      </>
                    )}
                    {c.status === "resolved" && (
                      <button className="link-btn" onClick={() => changeCommentStatus(c.id, "open")}>
                        Reopen
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="pb-brief-body">
        <div className="pb-brief-prose">
          <div className="pb-hero">
            <div className="pb-hero-eyebrow">TL;DR</div>
            <div className="pb-hero-tldr">
              {displayedTldr
                ? <InlineMarkdown>{displayedTldr}</InlineMarkdown>
                : <span className="muted-note">No TL;DR set.</span>}
            </div>
            {displayedHighlights.length > 0 && (
              <div className="pb-highlights">
                {displayedHighlights.map((h, i) => (
                  <div key={i} className={`pb-highlight pb-highlight-${h.tone}`}>
                    <span className="pb-highlight-icon">{highlightIcon(h.tone)}</span>
                    <span className="pb-highlight-text">{h.text}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {simplifyMode && artifact.simplified?.body ? (
            <>
              <div className="pb-simplified-banner">
                <strong>🧒 Plain-language rewrite.</strong>
                <span className="muted-note" style={{ marginLeft: 6 }}>
                  Same brief, every term defined inline.
                </span>
              </div>
              <div className="pb-simplified-body">
                <MarkdownView>{artifact.simplified.body}</MarkdownView>
              </div>
            </>
          ) : Object.keys(displayedSections).length > 0 ? (
            <div className="pb-sections">
              {Object.entries(displayedSections).map(([k, v], idx) => {
                const collapsed = !!collapsedSections[k];
                return (
                  <section key={k} className={collapsed ? "pb-section-collapsed" : ""}>
                    <h3 onClick={() => setCollapsedSections((s) => ({ ...s, [k]: !collapsed }))}>
                      <button
                        className="pb-section-toggle"
                        onClick={(e) => { e.stopPropagation(); setCollapsedSections((s) => ({ ...s, [k]: !collapsed })); }}
                        aria-label={collapsed ? "Expand" : "Collapse"}
                      >
                        {collapsed ? "▸" : "▾"}
                      </button>
                      <span className="pb-section-num">{idx + 1}.</span> {k}
                    </h3>
                    {!collapsed && (
                      <div className="pb-section-body">
                        <MarkdownView>{v || ""}</MarkdownView>
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="pb-sections">
              <section>
                <div className="pb-section-body">
                  <MarkdownView>{displayedRawMarkdown}</MarkdownView>
                </div>
              </section>
            </div>
          )}

          {/* Playbook research transcript — the brief's upstream steps shown
              as a connected tree so the linear chain of analysis is visible
              at a glance. Each node is collapsible: click to inspect the
              full output. Only rendered for artifacts emitted by a multi-step
              playbook run (others have no step_outputs in provenance). */}
          {artifact.provenance?.step_outputs && artifact.provenance.step_outputs.length > 0 && (
            <div className="pb-step-transcript">
              <h3 className="pb-step-transcript-head">
                <span className="pb-section-icon" aria-hidden>🌿</span>
                Research transcript
                <span className="muted-note" style={{ marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
                  {artifact.provenance.step_outputs.length} steps · click any node to expand
                </span>
              </h3>
              <ul className="pb-step-tree" role="tree">
                {artifact.provenance.step_outputs.map((step, idx) => {
                  const tok = step.tokens || { input: 0, output: 0 };
                  const isLast = idx === (artifact.provenance!.step_outputs!.length - 1);
                  return (
                    <li
                      key={step.id || idx}
                      className={`pb-step-node ${isLast ? "pb-step-node-last" : ""}`}
                      role="treeitem"
                    >
                      <details className="pb-step-output">
                        <summary>
                          <span className="pb-step-num">{idx + 1}</span>
                          <span className="pb-step-summary-text">
                            <span className="pb-step-label">{step.label}</span>
                            <span className="pb-step-meta">
                              {step.type}
                              {(tok.input || tok.output) ? ` · ${tok.input.toLocaleString()} in · ${tok.output.toLocaleString()} out` : ""}
                            </span>
                          </span>
                        </summary>
                        <div className="pb-step-body">
                          <MarkdownView>{step.output}</MarkdownView>
                          {step.web_sources && step.web_sources.length > 0 && (
                            <div className="pb-step-sources">
                              <strong>Web sources:</strong>
                              <ul>
                                {step.web_sources.map((s, i) => (
                                  <li key={i}>
                                    <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      </details>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      </div>

      <QaPanel
        history={artifact.qa_history || []}
        onAsk={submitQa}
        question={qaQuestion}
        onQuestionChange={setQaQuestion}
        busy={qaBusy}
      />

      {patchSuggestion && (
        <PatchSuggestionModal
          suggestion={patchSuggestion}
          applying={patchApplying}
          onApply={applySuggestion}
          onClose={() => setPatchSuggestion(null)}
        />
      )}
      {influenceOpen && artifact.provenance?.playbook_run_id && (
        <InfluenceDrawer
          open
          target={{ kind: "run", runId: artifact.provenance.playbook_run_id }}
          onClose={() => setInfluenceOpen(false)}
        />
      )}
    </div>
  );
}

function PatchSuggestionModal({
  suggestion, applying, onApply, onClose,
}: {
  suggestion: PatchSuggestion;
  applying: boolean;
  onApply: () => void;
  onClose: () => void;
}) {
  const empty = suggestion.suggested_changes.length === 0;
  return (
    <div className="art-modal-overlay" onClick={onClose}>
      <div className="art-modal art-modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="art-modal-head">
          <div>
            <h3>Suggested patch</h3>
            <span className="muted-note">
              From parent v{suggestion.from_version} → v{suggestion.to_version}
            </span>
          </div>
          <button className="link-btn" onClick={onClose}>✕</button>
        </header>
        <div className="art-modal-body">
          {suggestion.summary && (
            <div className="art-patch-summary">{suggestion.summary}</div>
          )}

          <h4 className="art-patch-h4">What changed upstream</h4>
          <ul className="art-patch-list">
            {suggestion.parent_changes.map((pc, i) => (
              <li key={i}>
                <div className="art-patch-section">{pc.section}</div>
                <details>
                  <summary>Before / after</summary>
                  <div className="art-diff-grid">
                    <div className="art-diff-cell">
                      <div className="art-diff-label">BEFORE</div>
                      <pre>{pc.before || "(empty)"}</pre>
                    </div>
                    <div className="art-diff-cell">
                      <div className="art-diff-label">AFTER</div>
                      <pre>{pc.after || "(empty)"}</pre>
                    </div>
                  </div>
                </details>
              </li>
            ))}
          </ul>

          <h4 className="art-patch-h4">Proposed patches to this artifact</h4>
          {empty ? (
            <div className="muted-note">No downstream changes needed.</div>
          ) : (
            <ul className="art-patch-list">
              {suggestion.suggested_changes.map((sc, i) => (
                <li key={i}>
                  <div className="art-patch-section">{sc.section}</div>
                  <div className="muted-note">{sc.rationale}</div>
                  <pre className="art-patch-proposed">{sc.proposed_text}</pre>
                </li>
              ))}
            </ul>
          )}
        </div>
        <footer className="art-modal-foot">
          <button className="btn-secondary small" onClick={onClose}>Close</button>
          <button
            className="btn-primary small"
            onClick={onApply}
            disabled={applying || empty}
            title={empty ? "Nothing to apply" : "Run refine with this suggestion as the instruction"}
          >
            {applying ? <><span className="spinner" /> Applying…</> : "Apply via refine"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function QaPanel({
  history, onAsk, question, onQuestionChange, busy,
}: {
  history: { id: string; question: string; answer: string; created_at: number }[];
  onAsk: () => void;
  question: string;
  onQuestionChange: (s: string) => void;
  busy: boolean;
}) {
  return (
    <section className="pb-qa-panel">
      <div className="pb-review-head">
        <strong>Follow-up Q&amp;A</strong>
        <span className="muted-note" style={{ marginLeft: 8 }}>
          Grounded in this artifact + its source run
        </span>
      </div>
      <div className="pb-review-composer">
        <textarea
          rows={2}
          placeholder="Ask a follow-up about this artifact…"
          value={question}
          onChange={(e) => onQuestionChange(e.target.value)}
        />
        <button
          className="btn-primary small"
          disabled={!question.trim() || busy}
          onClick={onAsk}
        >
          {busy ? <span className="spinner" /> : "Ask"}
        </button>
      </div>
      {history.length === 0 ? (
        <div className="muted-note">No questions asked yet.</div>
      ) : (
        <ul className="art-qa-list">
          {history.slice().reverse().map((q) => (
            <li key={q.id}>
              <div className="art-qa-q"><strong>Q:</strong> {q.question}</div>
              <div className="art-qa-a"><MarkdownView>{q.answer}</MarkdownView></div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CreateArtifactModal({
  types, onClose, onCreated, notify,
}: {
  types: Record<string, string>;
  onClose: () => void;
  onCreated: (id: string) => void;
  notify: ToastNotify;
}) {
  const [type, setType] = useState<string>("FreeformNote");
  const [title, setTitle] = useState("");
  const [tldr, setTldr] = useState("");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!body.trim()) {
      notify("error", "Body cannot be empty");
      return;
    }
    setSubmitting(true);
    try {
      const a = await api.createArtifact({
        artifact_type: type,
        title,
        tldr,
        sections: { Body: body },
        raw_markdown: `# ${title || types[type] || type}\n\n**TL;DR:** ${tldr}\n\n## Body\n${body}`,
      });
      onCreated(a.id);
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="art-modal-overlay" onClick={onClose}>
      <div className="art-modal" onClick={(e) => e.stopPropagation()}>
        <header className="art-modal-head">
          <h3>New artifact</h3>
          <button className="link-btn" onClick={onClose}>✕</button>
        </header>
        <div className="art-modal-body">
          <label className="art-field">
            <span>Type</span>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {Object.entries(types).map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </label>
          <label className="art-field">
            <span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Untitled" />
          </label>
          <label className="art-field">
            <span>TL;DR</span>
            <input value={tldr} onChange={(e) => setTldr(e.target.value)} placeholder="One sentence." />
          </label>
          <label className="art-field">
            <span>Body (Markdown)</span>
            <textarea
              rows={10}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="# Heading…"
            />
          </label>
        </div>
        <footer className="art-modal-foot">
          <button className="btn-secondary small" onClick={onClose}>Cancel</button>
          <button className="btn-primary small" onClick={submit} disabled={submitting || !body.trim()}>
            {submitting ? <><span className="spinner" /> Saving…</> : "Save artifact"}
          </button>
        </footer>
      </div>
    </div>
  );
}
