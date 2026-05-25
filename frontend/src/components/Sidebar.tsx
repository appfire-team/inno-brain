import { useState } from "react";
import { api, formatBytes, type IngestedRepo, type UploadedFile } from "../api";

type Props = {
  docs: UploadedFile[];
  repos: IngestedRepo[];
  uploading: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onUpload: (files: FileList | File[]) => void;
  onDelete: (name: string) => void;
  onUrlAdd: (url: string) => void;
  onWebResearch: (query: string) => Promise<void>;
  onIngestRepo: (path: string) => Promise<void>;
  onDeleteRepo: (name: string) => void;
};

export function Sidebar({
  docs, repos, uploading, collapsed, onToggleCollapsed,
  onUpload, onDelete, onUrlAdd, onWebResearch, onIngestRepo, onDeleteRepo,
}: Props) {
  const [dropActive, setDropActive] = useState(false);
  const [url, setUrl] = useState("");
  const [submittingUrl, setSubmittingUrl] = useState(false);
  const [researchQuery, setResearchQuery] = useState("");
  const [researching, setResearching] = useState(false);
  const [repoPath, setRepoPath] = useState("");
  const [ingestingRepo, setIngestingRepo] = useState(false);

  if (collapsed) {
    return (
      <aside className="sidebar sidebar-collapsed">
        <button
          className="rail-toggle"
          onClick={onToggleCollapsed}
          title="Expand sidebar"
          aria-label="Expand sidebar"
        >
          ›
        </button>
        <div className="rail-meta" title={`${docs.length} documents + ${repos.length} repos indexed`}>
          <span className="rail-count">{docs.length}</span>
          <span className="rail-count-label">docs</span>
          {repos.length > 0 && (
            <>
              <span className="rail-count">{repos.length}</span>
              <span className="rail-count-label">repos</span>
            </>
          )}
        </div>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <button
        className="rail-toggle rail-toggle-inline"
        onClick={onToggleCollapsed}
        title="Collapse sidebar"
        aria-label="Collapse sidebar"
      >
        ‹
      </button>
      <div className="sidebar-section sidebar-primary" data-onboarding-target="docs">
        <h2><span className="sh-icon">📤</span> Upload files</h2>
        <label
          className={`dropzone ${dropActive ? "active" : ""} ${uploading ? "uploading" : ""}`}
          onDragEnter={(e) => { e.preventDefault(); setDropActive(true); }}
          onDragOver={(e) => { e.preventDefault(); setDropActive(true); }}
          onDragLeave={() => setDropActive(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDropActive(false);
            if (e.dataTransfer.files) onUpload(e.dataTransfer.files);
          }}
        >
          <input
            type="file"
            multiple
            onChange={(e) => e.target.files && onUpload(e.target.files)}
            disabled={uploading}
          />
          <div className="dropzone-icon" aria-hidden>{uploading ? <span className="spinner" /> : "＋"}</div>
          <div className="dropzone-main">
            {uploading ? "Uploading…" : <><strong>Click to choose</strong> or drop files</>}
          </div>
          <div className="dropzone-hint">PDFs · markdown · text · code · max 50 MB each</div>
        </label>
      </div>

      <div className="sidebar-section">
        <h2><span className="sh-icon">🔗</span> Add by URL</h2>
        <form
          className="sidebar-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!url.trim() || submittingUrl) return;
            setSubmittingUrl(true);
            try {
              await onUrlAdd(url.trim());
              setUrl("");
            } finally {
              setSubmittingUrl(false);
            }
          }}
        >
          <input
            className="text-input"
            type="url"
            placeholder="https://arxiv.org/abs/… or tweet, PDF, page"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={submittingUrl || uploading}
          />
          <button
            type="submit"
            className="btn-secondary small full"
            disabled={submittingUrl || uploading || !url.trim()}
          >
            {submittingUrl ? <><span className="spinner" /> Fetching…</> : "↓ Fetch + add"}
          </button>
        </form>
      </div>

      <div className="sidebar-section">
        <h2><span className="sh-icon">💻</span> Add code repository</h2>
        <form
          className="sidebar-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!repoPath.trim() || ingestingRepo) return;
            setIngestingRepo(true);
            try {
              await onIngestRepo(repoPath.trim());
              setRepoPath("");
            } finally {
              setIngestingRepo(false);
            }
          }}
        >
          <input
            className="text-input"
            type="text"
            placeholder="/abs/path/to/your-repo"
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
            disabled={ingestingRepo || uploading}
            spellCheck={false}
          />
          <button
            type="submit"
            className="btn-secondary small full"
            disabled={ingestingRepo || uploading || !repoPath.trim()}
            title="Copy code + markdown from the directory into this workspace and rebuild the graph. Skips .git/node_modules/build/etc. and files >5MB."
          >
            {ingestingRepo ? <><span className="spinner" /> Ingesting…</> : "↳ Ingest repo"}
          </button>
        </form>
        {repos.length > 0 && (
          <ul className="doc-list repo-list" style={{ marginTop: 10 }}>
            {repos.map((r) => (
              <li key={r.name}>
                <span className="doc-icon" aria-hidden>📁</span>
                <span className="doc-name" title={r.name}>{r.name}</span>
                <span className="doc-size">{r.file_count} files · {formatBytes(r.bytes)}</span>
                <button onClick={() => onDeleteRepo(r.name)} disabled={uploading} title="Remove repo">×</button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="sidebar-section">
        <h2><span className="sh-icon">🌐</span> Research the web</h2>
        <form
          className="sidebar-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!researchQuery.trim() || researching) return;
            setResearching(true);
            try {
              await onWebResearch(researchQuery.trim());
              setResearchQuery("");
            } finally {
              setResearching(false);
            }
          }}
        >
          <input
            className="text-input"
            type="text"
            placeholder="e.g. 'FDA QMSR rollout status'"
            value={researchQuery}
            onChange={(e) => setResearchQuery(e.target.value)}
            disabled={researching || uploading}
          />
          <button
            type="submit"
            className="btn-secondary small full"
            disabled={researching || uploading || !researchQuery.trim()}
            title="Run a web search and save the findings as a new document in this workspace"
          >
            {researching ? <><span className="spinner" /> Researching…</> : "🌐 Research + add"}
          </button>
        </form>
      </div>

      <div className="sidebar-section flex-fill">
        <h2>
          <span className="sh-icon">📚</span> Indexed documents
          <span className="sh-count">{docs.length}</span>
        </h2>
        {docs.length === 0 ? (
          <div className="empty">
            <div className="empty-icon" aria-hidden>📄</div>
            <div>No documents yet.</div>
            <small>Drop files above or paste a URL.</small>
          </div>
        ) : (
          <ul className="doc-list">
            {docs.map((d) => (
              <li key={d.name}>
                <span className="doc-icon" aria-hidden>{fileIcon(d.name)}</span>
                <span className="doc-name" title={d.name}>{d.name}</span>
                <span className="doc-size">{formatBytes(d.size)}</span>
                <button onClick={() => onDelete(d.name)} disabled={uploading} title={`Remove ${d.name}`}>×</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

// Re-export api so the parent can keep imports tidy.
export { api };

// Pick a single-emoji icon by file extension. Falls back to a generic doc icon.
function fileIcon(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["pdf"].includes(ext)) return "📕";
  if (["md", "markdown", "mdx"].includes(ext)) return "📝";
  if (["txt", "rtf"].includes(ext)) return "📄";
  if (["docx", "doc"].includes(ext)) return "📃";
  if (["py", "ts", "tsx", "js", "jsx", "go", "rs", "java", "rb", "cpp", "c", "h", "hpp", "sh", "kt", "swift"].includes(ext)) return "💻";
  if (["html", "htm"].includes(ext)) return "🌐";
  if (["json", "yaml", "yml", "toml"].includes(ext)) return "⚙️";
  if (["csv", "tsv", "xlsx"].includes(ext)) return "📊";
  return "📄";
}
