import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api,
  getActiveWorkspaceId,
  setActiveWorkspaceId,
  type IndexJob,
  type IngestedRepo,
  type Insights,
  type Stats,
  type UploadedFile,
  type WorkspaceSummary,
} from "./api";
import { Sidebar } from "./components/Sidebar";
import { AskPanel } from "./components/AskPanel";
import { GraphPanel } from "./components/GraphPanel";
import { InsightsPanel } from "./components/InsightsPanel";
import { CommunitiesPanel } from "./components/CommunitiesPanel";
import { PathPanel } from "./components/PathPanel";
import { ExplainDrawer } from "./components/ExplainDrawer";
import { ConversationsPanel } from "./components/ConversationsPanel";
import { ForeSightPanel } from "./components/ForeSightPanel";
import { GuidePanel } from "./components/GuidePanel";
import { PlaybooksPanel } from "./components/PlaybooksPanel";
import { ArtifactsPanel } from "./components/ArtifactsPanel";
import { RefineKBPanel } from "./components/RefineKBPanel";
import { WorkspaceSwitcher } from "./components/WorkspaceSwitcher";
import { useCollapsed } from "./hooks/useCollapsed";
import { MemoryDrawer } from "./components/MemoryDrawer";
import { RubricManager } from "./components/RubricManager";
import { OnboardingChecklist } from "./components/OnboardingChecklist";
import { WelcomeModal } from "./components/WelcomeModal";

type Tab = "playbooks" | "ask" | "conversations" | "foresight" | "artifacts" | "graph" | "communities" | "insights" | "path" | "refine" | "guide";
type Toast = { kind: "success" | "error" | "info"; message: string } | null;

const EXEC_MODE_KEY = "innobrain.execMode";

// Deep-link params. The artifact viewer's "🔗 Copy link" button builds a URL
// of the form ?ws=<workspace-id>&tab=artifacts&artifact=<artifact-id>; we
// parse them here once on mount so a teammate clicking that link lands on the
// right workspace, tab, and artifact without manual navigation.
const VALID_TABS: Tab[] = [
  "playbooks", "ask", "conversations", "foresight", "artifacts",
  "graph", "communities", "insights", "path", "refine", "guide",
];
function parseDeepLink(): { ws: string | null; tab: Tab | null; artifact: string | null } {
  if (typeof window === "undefined") return { ws: null, tab: null, artifact: null };
  const p = new URLSearchParams(window.location.search);
  const ws = p.get("ws");
  const tabRaw = p.get("tab");
  const tab = tabRaw && (VALID_TABS as string[]).includes(tabRaw) ? (tabRaw as Tab) : null;
  const artifact = p.get("artifact");
  return { ws, tab, artifact };
}

export function App() {
  // Resolved once per mount — URL drives initial tab + workspace + artifact.
  const deepLink = useRef(parseDeepLink());
  const [activeWorkspace, setActiveWorkspaceState] = useState<WorkspaceSummary | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [docs, setDocs] = useState<UploadedFile[]>([]);
  const [repos, setRepos] = useState<IngestedRepo[]>([]);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [tab, setTab] = useState<Tab>(deepLink.current.tab ?? "conversations");
  // One-shot: ArtifactsPanel reads this on first mount to focus the linked
  // artifact, then we clear it so later workspace switches don't keep
  // re-applying the original deep-link target.
  const [initialArtifactId, setInitialArtifactId] = useState<string | null>(
    deepLink.current.artifact,
  );
  const [askPrefill, setAskPrefill] = useState<string>("");
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<Toast>(null);
  const [indexJob, setIndexJob] = useState<IndexJob | null>(null);
  // Transient "✓ Indexed" banner shown for ~12s after a job completes. A
  // second visual channel for completion — the toast can be missed if the
  // user is away from the screen during the 5-9s auto-dismiss window.
  const [lastCompletion, setLastCompletion] = useState<IndexJob | null>(null);
  // Set of job IDs we've already shown a completion toast for. Persisted in
  // localStorage so a page refresh after the job finished doesn't replay the
  // toast — but ALSO doesn't suppress it for a job whose completion the user
  // has never been notified about.
  const toastedJobIds = useRef<Set<string>>(new Set());
  // Load on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem("innobrain.toastedJobs");
      if (raw) toastedJobIds.current = new Set(JSON.parse(raw));
    } catch { /* corrupt storage → start fresh */ }
  }, []);
  const [explainOpen, setExplainOpen] = useState(false);
  const [explainQuery, setExplainQuery] = useState<string | null>(null);
  const [focusedCommunity, setFocusedCommunity] = useState<number | null>(null);
  const [sidebarCollapsed, toggleSidebar] = useCollapsed("main-sidebar", false);
  const [wideMode, toggleWideMode] = useCollapsed("wide-mode", false);
  const [foresightPrefill, setForesightPrefill] = useState<{
    scenario: string;
    conversationId?: string;
    conversationTitle?: string;
  } | null>(null);
  const [refinePrefill, setRefinePrefill] = useState<import("./components/RefineKBPanel").RefinePrefill | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [rubricsOpen, setRubricsOpen] = useState(false);
  // Bumped whenever the rubrics drawer is opened. OnboardingChecklist watches
  // this to mark the rubric step complete.
  const [rubricsOpenedTick, setRubricsOpenedTick] = useState(0);
  // Bumped when onboarding's "Create a workspace" CTA fires — the switcher
  // watches this and opens the create form.
  const [createWorkspaceTick, setCreateWorkspaceTick] = useState(0);
  // Bumped by the header's "Quickstart" button to re-open the dismissed
  // onboarding checklist for the active workspace.
  const [reopenQuickstartTick, setReopenQuickstartTick] = useState(0);
  const [conversationsBusy, setConversationsBusy] = useState(false);
  const [playbooksBusy, setPlaybooksBusy] = useState(false);
  const [execMode, setExecMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    // Default to Exec mode on first visit; persist the user's later toggles so
    // power users who flip it off stay off.
    const raw = window.localStorage.getItem(EXEC_MODE_KEY);
    if (raw == null) return true;
    return raw === "1";
  });
  // When entering Exec mode, default to the Playbooks tab — the whole point.
  // Exception: a deep link with an explicit ?tab=… on initial mount wins,
  // so a "🔗 Copy link" to an artifact lands you on the artifact, not on
  // Playbooks just because exec mode is the default.
  const deepLinkTabHonored = useRef(false);
  useEffect(() => {
    window.localStorage.setItem(EXEC_MODE_KEY, execMode ? "1" : "0");
    if (!deepLinkTabHonored.current && deepLink.current.tab) {
      deepLinkTabHonored.current = true;
      return;
    }
    if (execMode && tab !== "playbooks") setTab("playbooks");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [execMode]);

  const refresh = useCallback(async () => {
    try {
      const [s, d, i] = await Promise.all([
        api.stats(),
        api.docs(),
        api.insights().catch(() => ({})),
      ]);
      setStats(s);
      setDocs(d.files);
      setRepos(d.repos ?? []);
      setInsights(i as Insights);
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    }
  }, []);

  // Resolve the active workspace on mount (or whenever it changes via the switcher).
  // Preference order: ?ws=<id> from the URL (deep link) → localStorage → first.
  const refreshWorkspace = useCallback(async () => {
    try {
      const { workspaces } = await api.workspaces();
      const stored = getActiveWorkspaceId();
      const linkWs = deepLink.current.ws;
      const linkMatch = linkWs ? workspaces.find((w) => w.id === linkWs) : null;
      const active = linkMatch ?? workspaces.find((w) => w.id === stored) ?? workspaces[0] ?? null;
      if (active && stored !== active.id) setActiveWorkspaceId(active.id);
      setActiveWorkspaceState(active);
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    }
  }, []);

  useEffect(() => { refreshWorkspace(); }, [refreshWorkspace]);
  useEffect(() => {
    if (activeWorkspace) refresh();
  }, [activeWorkspace?.id, refresh]);

  // Indexing-job poller. Polls every 3s when a job is running, every 15s
  // otherwise. On completion/failure, surface a one-time toast keyed by
  // job ID so:
  //   - refreshing the page during a running job still gets the completion toast,
  //   - refreshing the page AFTER an already-finished job doesn't replay it.
  useEffect(() => {
    if (!activeWorkspace) return;
    let cancelled = false;
    let timer: number | null = null;

    const persistToasted = () => {
      try {
        window.localStorage.setItem(
          "innobrain.toastedJobs",
          JSON.stringify(Array.from(toastedJobIds.current).slice(-200)),
        );
      } catch { /* localStorage may be unavailable */ }
    };

    const tick = async () => {
      let nextDelay = 15000;
      try {
        const j = await api.indexJob();
        if (cancelled) return;
        setIndexJob(j);
        nextDelay = j.status === "running" ? 3000 : 15000;

        // One-shot completion/failure notice per job id.
        if (
          j.id &&
          (j.status === "complete" || j.status === "failed") &&
          !toastedJobIds.current.has(j.id)
        ) {
          toastedJobIds.current.add(j.id);
          persistToasted();
          if (j.status === "complete") {
            setToast({ kind: "success", message: `Indexing complete — ${j.label || j.kind || "graph"}` });
            // Latch a green banner so the completion survives the toast's
            // auto-dismiss window. Cleared by the timer in the lastCompletion
            // effect below.
            setLastCompletion(j);
          } else {
            setToast({ kind: "error", message: `Indexing failed: ${j.error || j.message || "unknown error"}` });
          }
          await refresh();
        }
      } catch { /* poll silently on transient failures */ }

      if (!cancelled) timer = window.setTimeout(tick, nextDelay);
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id]);

  const isBusy = indexJob?.status === "running";

  // Auto-dismiss success/info after 9s; keep error toasts sticky so the user
  // has time to read and copy the message. Errors are dismissed only by the
  // close button below. (Bumped from 5s because long-running rebuilds finish
  // when the user is away from the screen — the extra window helps them
  // catch the completion notice.)
  useEffect(() => {
    if (!toast) return;
    if (toast.kind === "error") return;
    const t = setTimeout(() => setToast(null), 9000);
    return () => clearTimeout(t);
  }, [toast]);

  // The "✓ Indexed" success banner lingers for 12s after completion, then
  // clears itself. Keeps the success state visible even if the toast is
  // gone — a second channel so completion can't go unnoticed.
  useEffect(() => {
    if (!lastCompletion) return;
    const t = setTimeout(() => setLastCompletion(null), 12000);
    return () => clearTimeout(t);
  }, [lastCompletion]);

  // Persist the last error to localStorage so the user can recover the
  // message even after a page refresh ("the error went away too fast").
  useEffect(() => {
    if (toast?.kind === "error" && typeof window !== "undefined") {
      try {
        window.localStorage.setItem(
          "innobrain.lastError",
          JSON.stringify({ message: toast.message, at: Date.now() }),
        );
      } catch { /* localStorage may be unavailable */ }
    }
  }, [toast]);

  // All ingest/rebuild/delete actions return immediately with an indexing
  // job descriptor. We push that into `indexJob` state right away so the
  // banner appears instantly (no need to wait for the next poll). The poller
  // takes over from there and surfaces completion/failure toasts.
  const handleUpload = async (files: FileList | File[]) => {
    const arr = Array.from(files);
    if (arr.length === 0) return;
    setUploading(true);
    try {
      const res = await api.upload(arr);
      if (res.job) setIndexJob(res.job);
      setToast({ kind: "info", message: `Uploaded ${arr.length} file${arr.length === 1 ? "" : "s"}. Indexing in the background…` });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const handleUrlAdd = async (url: string) => {
    setUploading(true);
    try {
      const res = await api.ingestUrl(url);
      if (res.job) setIndexJob(res.job);
      setToast({ kind: "info", message: `Fetched ${res.saved}. Indexing in the background…` });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const handleIngestRepo = async (path: string) => {
    setUploading(true);
    try {
      const r = await api.ingestRepo(path);
      if (r.job) setIndexJob(r.job);
      setToast({
        kind: "info",
        message: `Copied ${r.copied} file${r.copied === 1 ? "" : "s"} from ${r.repo}. Indexing in the background…`,
      });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteRepo = async (name: string) => {
    if (!confirm(`Remove repo "${name}" and rebuild the graph?`)) return;
    try {
      const r = await api.deleteRepo(name);
      if (r.job) setIndexJob(r.job);
      setToast({ kind: "info", message: `Removed ${name}. Re-indexing in the background…` });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    }
  };

  const handleWebResearch = async (query: string) => {
    setUploading(true);
    setToast({ kind: "info", message: `Researching the web: "${query}"…` });
    try {
      const res = await api.webResearch(query);
      if (res.job) setIndexJob(res.job);
      setToast({
        kind: "info",
        message: `Saved web research (${res.web_sources.length} sources). Indexing in the background…`,
      });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (filename: string) => {
    if (!confirm(`Remove ${filename} and rebuild the graph?`)) return;
    try {
      const r = await api.deleteDoc(filename);
      if (r.job) setIndexJob(r.job);
      setToast({ kind: "info", message: `Removed ${filename}. Re-indexing in the background…` });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    }
  };

  const [rebuilding, setRebuilding] = useState(false);
  const handleRebuild = async () => {
    if (rebuilding) return;
    if (!confirm("Rebuild the knowledge graph from scratch? Re-runs AST + LLM extraction over every file. Heavy on tokens.")) return;
    setRebuilding(true);
    try {
      const r = await api.rebuild();
      if (r.job) setIndexJob(r.job);
      setToast({ kind: "info", message: "Rebuild started. Tracking progress in the banner above." });
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setRebuilding(false);
    }
  };

  const [relinking, setRelinking] = useState(false);
  const handleRelink = async () => {
    if (relinking) return;
    setRelinking(true);
    setToast({ kind: "info", message: "Linking entities across documents… (~30-60s)" });
    try {
      const r = await api.relink();
      if (r.error) {
        setToast({ kind: "error", message: r.error });
      } else {
        const componentsLine = r.components_before !== r.components_after
          ? `, ${r.components_before} → ${r.components_after} components`
          : "";
        setToast({
          kind: "success",
          message: `+${r.edges_added} cross-doc edges${componentsLine} (${r.communities_after} communities)`,
        });
      }
      await refresh();
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
    } finally {
      setRelinking(false);
    }
  };

  const openNode = (label: string) => {
    setExplainQuery(label);
    setExplainOpen(true);
  };

  const askPrefilled = (q: string) => {
    setAskPrefill(q);
    setTab("ask");
  };

  return (
    <div className={`app ${sidebarCollapsed ? "sidebar-is-collapsed" : ""} ${wideMode ? "wide-mode" : ""}`}>
      <header className="header">
        <h1>
          <span className="logo-mark">◉</span> InnoBrain
          {!execMode && <span> · {"<shared brain/>"}</span>}
        </h1>
        <WorkspaceSwitcher
          active={activeWorkspace}
          onChanged={refreshWorkspace}
          onNotify={(kind, message) => setToast({ kind, message })}
          openCreateSignal={createWorkspaceTick}
        />
        <div className="stats-pills">
          {!execMode && (
            <>
              {(() => {
                // Show only non-zero source-type pills so a fresh workspace
                // doesn't spam four "0" chips. `sources` is always present
                // on fresh responses; fall back to `files` for older shapes.
                const src = stats?.sources;
                if (!src) {
                  return <div className="pill"><strong>{stats?.files ?? 0}</strong> files</div>;
                }
                const pills: ReactNode[] = [];
                if (src.repos)    pills.push(<div className="pill" key="r" title="Code repositories ingested"><strong>{src.repos}</strong> 📁 repos</div>);
                if (src.docs)     pills.push(<div className="pill" key="d" title="Uploaded documents (PDFs, markdown, etc.)"><strong>{src.docs}</strong> 📄 docs</div>);
                if (src.urls)     pills.push(<div className="pill" key="u" title="URLs fetched via the link input"><strong>{src.urls}</strong> 🔗 links</div>);
                if (src.research) pills.push(<div className="pill" key="w" title="Web-research outputs"><strong>{src.research}</strong> 🌐 research</div>);
                if (pills.length === 0) {
                  pills.push(<div className="pill" key="f"><strong>0</strong> files</div>);
                }
                return <>{pills}</>;
              })()}
              <div className="pill"><strong>{stats?.nodes ?? 0}</strong> nodes</div>
              <div className="pill"><strong>{stats?.edges ?? 0}</strong> edges</div>
              <div className="pill"><strong>{stats?.communities ?? 0}</strong> communities</div>
            </>
          )}
          {execMode && (() => {
            // Exec mode: same source-type breakdown, compact. Single "indexed"
            // pill when there's nothing else worth showing.
            const src = stats?.sources;
            if (!src || (src.docs + src.urls + src.research + src.repos === 0)) {
              return <div className="pill"><strong>{stats?.files ?? 0}</strong> docs indexed</div>;
            }
            const parts: string[] = [];
            if (src.repos)    parts.push(`${src.repos} 📁`);
            if (src.docs)     parts.push(`${src.docs} 📄`);
            if (src.urls)     parts.push(`${src.urls} 🔗`);
            if (src.research) parts.push(`${src.research} 🌐`);
            return <div className="pill" title="Repos · Docs · Links · Research indexed"><strong>{parts.join(" · ")}</strong> indexed</div>;
          })()}
          <button
            className="btn-header btn-header-quickstart"
            onClick={() => setReopenQuickstartTick((x) => x + 1)}
            title="Re-open the Quickstart checklist for this workspace"
          >
            🚀 Quickstart
          </button>
          <a
            className="btn-header"
            href="/onboarding.html"
            target="_blank"
            rel="noreferrer"
            title="Open the full onboarding + user guide in a new tab"
          >
            📖 Guide
          </a>
          <a
            className="btn-header"
            href="/presentation.html"
            target="_blank"
            rel="noreferrer"
            title="Open the InnoBrain pitch deck in a new tab"
          >
            🎤 Pitch
          </a>
          <button
            className="btn-header"
            onClick={() => setMemoryOpen(true)}
            title="Persistent memory injected into every LLM call in this workspace"
          >
            🧠 Memory
          </button>
          <button
            className="btn-header"
            onClick={() => { setRubricsOpen(true); setRubricsOpenedTick((t) => t + 1); }}
            title="Framing rules folded into every LLM call in this workspace"
          >
            📐 Rubrics
          </button>
          <label className="exec-toggle" title="Simplified view for execs">
            <input
              type="checkbox"
              checked={execMode}
              onChange={(e) => setExecMode(e.target.checked)}
            />
            <span>Exec mode</span>
          </label>
          {!execMode && (
            <button
              className="btn-header"
              onClick={handleRelink}
              disabled={relinking || uploading || rebuilding || isBusy || !stats?.has_graph}
              title="Run cross-document linker to add edges between same entities mentioned in different documents"
            >
              {relinking ? <><span className="spinner" /> Linking…</> : "↻ Link docs"}
            </button>
          )}
          {!execMode && (
            <button
              className="btn-header"
              onClick={handleRebuild}
              disabled={rebuilding || uploading || relinking || isBusy || !stats?.has_graph}
              title="Rebuild the graph from scratch — re-runs AST + LLM extraction over every file. Use after prompts/models change or manual edits to data/raw."
            >
              {rebuilding ? <><span className="spinner" /> Rebuilding…</> : "↻ Rebuild"}
            </button>
          )}
        </div>
      </header>

      {memoryOpen && (
        <MemoryDrawer
          key={`mem-${activeWorkspace?.id ?? "no-ws"}`}
          open
          onClose={() => setMemoryOpen(false)}
        />
      )}
      {rubricsOpen && (
        <RubricManager
          key={`rub-${activeWorkspace?.id ?? "no-ws"}`}
          open
          onClose={() => setRubricsOpen(false)}
        />
      )}

      <Sidebar
        docs={docs}
        repos={repos}
        uploading={uploading || isBusy}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebar}
        onUpload={handleUpload}
        onDelete={handleDelete}
        onUrlAdd={handleUrlAdd}
        onWebResearch={handleWebResearch}
        onIngestRepo={handleIngestRepo}
        onDeleteRepo={handleDeleteRepo}
      />

      <main className="main">
        {uploading && (
          <div className="processing-banner">
            <span className="spinner" />
            Uploading…
          </div>
        )}
        {isBusy && (
          <div className="processing-banner indexing-banner">
            <span className="spinner" />
            <span>
              <strong>Indexing in progress</strong> — {indexJob?.label || indexJob?.kind || "graph"}
              {indexJob?.message ? <> · {indexJob.message}</> : null}
              . Search and queries will return partial or empty results until this finishes.
            </span>
          </div>
        )}
        {!isBusy && indexJob?.status === "failed" && (
          <div className="processing-banner indexing-banner indexing-banner-failed">
            <span>⚠</span>
            <span>
              <strong>Last indexing run failed.</strong>{" "}
              {indexJob.error || indexJob.message || "Unknown error."}{" "}
              <button
                className="link-btn"
                onClick={() => setIndexJob({ ...indexJob, status: "idle" })}
              >
                dismiss
              </button>
            </span>
          </div>
        )}
        {!isBusy && lastCompletion && (
          <div className="processing-banner indexing-banner indexing-banner-complete">
            <span>✓</span>
            <span>
              <strong>Indexing complete</strong>
              {lastCompletion.label || lastCompletion.kind
                ? <> — {lastCompletion.label || lastCompletion.kind}</>
                : null}
              {stats?.has_graph && (
                <> · {stats.nodes} nodes · {stats.edges} edges · {stats.communities} communities</>
              )}{" "}
              <button
                className="link-btn"
                onClick={() => setLastCompletion(null)}
              >
                dismiss
              </button>
            </span>
          </div>
        )}

        <nav className="tabs">
          <button
            data-tab="playbooks"
            className={`${tab === "playbooks" ? "active" : ""} ${playbooksBusy ? "tab-busy" : ""}`}
            onClick={() => setTab("playbooks")}
            title={playbooksBusy ? "A playbook run is in flight" : undefined}
          >
            {playbooksBusy && <span className="spinner tab-spinner" aria-hidden />}
            Playbooks
          </button>
          <button
            className={`${tab === "conversations" ? "active" : ""} ${conversationsBusy ? "tab-busy" : ""}`}
            onClick={() => setTab("conversations")}
            title={conversationsBusy ? "A turn is in flight in Conversations" : undefined}
          >
            {conversationsBusy && <span className="spinner tab-spinner" aria-hidden />}
            Conversations
          </button>
          <button className={tab === "foresight" ? "active" : ""} onClick={() => setTab("foresight")}>
            ForeSight
          </button>
          <button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")}>
            Artifacts
          </button>
          {!execMode && (
            <button className={tab === "ask" ? "active" : ""} onClick={() => setTab("ask")}>Ask Graph</button>
          )}
          {!execMode && (
            <button className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}>Graph</button>
          )}
          {!execMode && (
            <button className={tab === "communities" ? "active" : ""} onClick={() => setTab("communities")}>Communities</button>
          )}
          {!execMode && (
            <button className={tab === "insights" ? "active" : ""} onClick={() => setTab("insights")}>
              Insights
            </button>
          )}
          {!execMode && (
            <button className={tab === "path" ? "active" : ""} onClick={() => setTab("path")}>Path</button>
          )}
          <button data-tab="refine" className={tab === "refine" ? "active" : ""} onClick={() => setTab("refine")}>
            Refine KB
          </button>
          <button className={tab === "guide" ? "active" : ""} onClick={() => setTab("guide")}>
            Guide
          </button>
        </nav>

        <div className="tab-content" key={activeWorkspace?.id ?? "no-ws"}>
          {tab === "playbooks" && (
            <PlaybooksPanel onBusyChange={setPlaybooksBusy} />
          )}
          {tab === "ask" && (
            <AskPanel
              stats={stats}
              insights={insights}
              initialQuestion={askPrefill}
              onNodeClick={openNode}
              key={askPrefill}
            />
          )}
          {/* ConversationsPanel stays mounted across tab switches so in-flight
              turns/simulations don't get dropped — only its visibility toggles. */}
          <div style={{ display: tab === "conversations" ? "contents" : "none" }}>
            <ConversationsPanel
              onNodeClick={openNode}
              wideMode={wideMode}
              onToggleWideMode={toggleWideMode}
              onBusyChange={setConversationsBusy}
              onAdvancedSimulate={(scenario, conversationId, conversationTitle) => {
                setForesightPrefill({ scenario, conversationId, conversationTitle });
                setTab("foresight");
              }}
              onSaveAsKBFact={(prefill) => {
                setRefinePrefill(prefill);
                setTab("refine");
              }}
              onNotify={(kind, message) => setToast({ kind, message })}
            />
          </div>
          {tab === "foresight" && (
            <ForeSightPanel
              prefill={foresightPrefill}
              onPrefillConsumed={() => setForesightPrefill(null)}
              onNotify={(kind, message) => setToast({ kind, message })}
            />
          )}
          {tab === "artifacts" && (
            <ArtifactsPanel
              onNotify={(kind, message) => setToast({ kind, message })}
              initialArtifactId={initialArtifactId}
              onInitialArtifactConsumed={() => setInitialArtifactId(null)}
            />
          )}
          {tab === "graph" && (
            <GraphPanel onNodeClick={openNode} focusedCommunity={focusedCommunity} />
          )}
          {tab === "communities" && (
            <CommunitiesPanel
              onFocusCommunity={(id) => { setFocusedCommunity(id); setTab("graph"); }}
              onNodeClick={openNode}
            />
          )}
          {tab === "insights" && (
            <InsightsPanel onAskQuestion={askPrefilled} onNodeClick={openNode} />
          )}
          {tab === "path" && <PathPanel onNodeClick={openNode} />}
          {tab === "refine" && (
            <RefineKBPanel
              onNodeClick={openNode}
              prefill={refinePrefill}
              onPrefillConsumed={() => setRefinePrefill(null)}
            />
          )}
          {tab === "guide" && <GuidePanel />}
        </div>
      </main>

      <ExplainDrawer
        open={explainOpen}
        query={explainQuery}
        onClose={() => setExplainOpen(false)}
        onNodeClick={openNode}
      />

      <OnboardingChecklist
        workspaceId={activeWorkspace?.id ?? null}
        filesCount={stats?.files ?? 0}
        rubricsOpenedTick={rubricsOpenedTick}
        onNavigateTab={(t) => setTab(t)}
        onOpenRubrics={() => { setRubricsOpen(true); setRubricsOpenedTick((x) => x + 1); }}
        onCreateWorkspace={() => setCreateWorkspaceTick((x) => x + 1)}
        reopenTick={reopenQuickstartTick}
      />

      <WelcomeModal
        onCreateWorkspace={() => setCreateWorkspaceTick((x) => x + 1)}
        reopenTick={reopenQuickstartTick}
      />

      {toast && (
        <div className={`toast ${toast.kind}`}>
          <span className="toast-message">{toast.message}</span>
          <button
            className="toast-close"
            onClick={() => setToast(null)}
            aria-label="Dismiss"
            title="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
