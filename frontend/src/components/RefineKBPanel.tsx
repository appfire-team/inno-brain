import { useEffect, useMemo, useState } from "react";
import {
  api,
  type KBConfidence,
  type KBCorrection,
  type KBDiff,
  type KBNodeMatch,
  type KBRefinementKind,
  type KBSourceType,
} from "../api";

export type RefinePrefill = {
  kind: KBRefinementKind;
  target_node_id?: string | null;
  target_node_label?: string | null;
  original_summary?: string;
  new_summary?: string;
  reason?: string;
  source_type?: KBSourceType;
};

type Props = {
  onNodeClick?: (label: string) => void;
  prefill?: RefinePrefill | null;
  onPrefillConsumed?: () => void;
};

type ComposerState = {
  mode: "create" | "edit";
  draft: Partial<KBCorrection>;
  // Track the picked node separately so we can render its label even when the
  // user clears the search field. `null` = unset (e.g. an `addition`).
  pickedNode: KBNodeMatch | null;
  originalId?: string;
};

// User-facing labels — verbs, not nouns. The schema kinds stay internal.
const KIND_ACTION: Record<KBRefinementKind, string> = {
  correction: "Fix",
  addition: "Add",
  attestation: "Confirm",
  dissent: "Doubt",
};
const KIND_LABEL: Record<KBRefinementKind, string> = {
  correction: "Correction",
  addition: "Addition",
  attestation: "Confirmation",
  dissent: "Doubt",
};
const KIND_ICON: Record<KBRefinementKind, string> = {
  correction: "✎",
  addition: "+",
  attestation: "✓",
  dissent: "?",
};
const KIND_TAGLINE: Record<KBRefinementKind, string> = {
  correction: "Replace something the KB got wrong.",
  addition: "Add something the KB doesn't know yet.",
  attestation: "Confirm a fact you've verified.",
  dissent: "Flag a doubt without overriding.",
};
// Plain-language prompts for the single content field per kind.
const KIND_PROMPT: Record<KBRefinementKind, string> = {
  correction: "What's the corrected fact?",
  addition: "What's the new fact?",
  attestation: "What did you verify?",
  dissent: "What's your concern?",
};
const KIND_PLACEHOLDER: Record<KBRefinementKind, string> = {
  correction: "e.g. Marketplace ARR is ≈ $300M, not $250M.",
  addition: "e.g. JMWE's main appeal is the workflow-builder UI, not the rules engine.",
  attestation: "e.g. Yes, I confirm the Opsgenie EOL date of Apr 5 2027.",
  dissent: "e.g. I'm not sure the $250M ARR figure is current.",
};

const SOURCE_TYPE_LABEL: Record<KBSourceType, string> = {
  human: "Human experience",
  document: "From a document",
  web: "From the web",
  kb_audit: "From a KB audit",
};

const AUTHOR_LS_KEY = "refine-kb.author";
const AUTHOR_BASIS_LS_KEY = "refine-kb.author-basis";

function blankDraft(): Partial<KBCorrection> {
  // Auto-prefill author / basis from prior usage so the form feels "warm" for
  // the second+ entry. Source type is always "human" when entered manually —
  // the audit playbook sets "kb_audit" programmatically.
  const author = (typeof window !== "undefined" && window.localStorage.getItem(AUTHOR_LS_KEY)) || "";
  const author_basis = (typeof window !== "undefined" && window.localStorage.getItem(AUTHOR_BASIS_LS_KEY)) || "";
  return {
    kind: "correction",
    target_node_id: null,
    source_type: "human",
    author,
    author_basis,
    confidence: "medium",
    original_summary: "",
    new_summary: "",
    reason: "",
    evidence_url: null,
  };
}

type RefineView = "facts" | "refinements";

export function RefineKBPanel({ onNodeClick, prefill, onPrefillConsumed }: Props) {
  const [view, setView] = useState<RefineView>("facts");
  const [corrections, setCorrections] = useState<KBCorrection[]>([]);
  const [diff, setDiff] = useState<KBDiff | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [composer, setComposer] = useState<ComposerState | null>(null);
  const [filterKind, setFilterKind] = useState<KBRefinementKind | "all">("all");
  const [search, setSearch] = useState("");

  const reload = async () => {
    try {
      const [r, d] = await Promise.all([api.kbCorrections(), api.kbDiff()]);
      setCorrections(r.corrections);
      setDiff(d.diff);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => { reload(); }, []);

  // Hydrate the composer when a prefill arrives from another tab
  // (e.g. "Save as KB fact" gesture from a conversation turn).
  useEffect(() => {
    if (!prefill) return;
    setComposer({
      mode: "create",
      draft: {
        ...blankDraft(),
        kind: prefill.kind,
        target_node_id: prefill.target_node_id ?? null,
        original_summary: prefill.original_summary ?? "",
        new_summary: prefill.new_summary ?? "",
        reason: prefill.reason ?? "",
        source_type: prefill.source_type ?? "human",
      },
      pickedNode: prefill.target_node_id
        ? { id: prefill.target_node_id, label: prefill.target_node_label ?? prefill.target_node_id }
        : null,
    });
    onPrefillConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const filtered = useMemo(() => {
    let rows = corrections;
    if (filterKind !== "all") rows = rows.filter((r) => r.kind === filterKind);
    const needle = search.trim().toLowerCase();
    if (needle) {
      rows = rows.filter((r) =>
        [r.target_node_id, r.original_summary, r.new_summary, r.author, r.reason]
          .some((v) => (v || "").toLowerCase().includes(needle)),
      );
    }
    return rows;
  }, [corrections, filterKind, search]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: corrections.length };
    for (const r of corrections) c[r.kind] = (c[r.kind] ?? 0) + 1;
    return c;
  }, [corrections]);

  const startNew = (kind: KBRefinementKind = "correction") =>
    setComposer({ mode: "create", draft: { ...blankDraft(), kind }, pickedNode: null });

  // Open the composer pre-targeted at a specific graph node + chosen kind.
  // Used by the Facts list's row actions.
  const openComposerForNode = (n: KBNodeMatch, kind: KBRefinementKind) =>
    setComposer({
      mode: "create",
      draft: {
        ...blankDraft(),
        kind,
        target_node_id: n.id,
        original_summary: n.label,
      },
      pickedNode: n,
    });

  const startEdit = (c: KBCorrection) =>
    setComposer({
      mode: "edit",
      draft: { ...c },
      pickedNode: c.target_node_id
        ? { id: c.target_node_id, label: c.target_node_id }
        : null,
      originalId: c.id,
    });

  const save = async () => {
    if (!composer) return;
    try {
      // Persist author / basis so the next refinement comes pre-filled.
      if (typeof window !== "undefined") {
        if (composer.draft.author) {
          window.localStorage.setItem(AUTHOR_LS_KEY, composer.draft.author);
        }
        if (composer.draft.author_basis) {
          window.localStorage.setItem(AUTHOR_BASIS_LS_KEY, composer.draft.author_basis);
        }
      }
      if (composer.mode === "create") {
        await api.createKBCorrection(composer.draft);
      } else if (composer.originalId) {
        await api.updateKBCorrection(composer.originalId, composer.draft);
      }
      setComposer(null);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (c: KBCorrection) => {
    if (!confirm(`Delete ${KIND_LABEL[c.kind].toLowerCase()}? Original graph fact will resurface in answers.`)) return;
    try {
      await api.deleteKBCorrection(c.id);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (composer) {
    return (
      <Composer
        state={composer}
        onChange={(next) => setComposer(next)}
        onCancel={() => setComposer(null)}
        onSave={save}
        error={error}
      />
    );
  }

  return (
    <div className="refine-kb">
      <header className="refine-kb-head">
        <div>
          <h2>Refine KB</h2>
          <p className="muted-note">
            The graph is a read-only extraction of your documents. Refinements live here and apply at read time —
            answers see your overrides without changing the underlying graph. Delete a row to restore the original.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button className="btn-primary small" onClick={() => startNew("correction")}>+ Tell the KB something</button>
        </div>
      </header>

      <div className="refine-subtabs">
        <button
          className={`subtab ${view === "facts" ? "active" : ""}`}
          onClick={() => setView("facts")}
          title="Browse every fact in the graph — review and refine in-place"
        >
          Facts in the KB
        </button>
        <button
          className={`subtab ${view === "refinements" ? "active" : ""}`}
          onClick={() => setView("refinements")}
        >
          My refinements <span className="subtab-count">{corrections.length}</span>
        </button>
      </div>

      {view === "facts" && (
        <FactsList
          corrections={corrections}
          onActOnNode={openComposerForNode}
          onEditExisting={startEdit}
          onOpenNode={(label) => onNodeClick?.(label)}
        />
      )}

      {view === "refinements" && (
        <>

      {diff && (diff.added.length || diff.removed.length || diff.relabeled.length) ? (
        <div className="kb-diff-banner">
          <div className="kb-diff-head" onClick={() => setDiffOpen((x) => !x)}>
            <strong>Last rebuild changed the KB</strong>
            <span className="muted-note">
              {diff.added.length} added · {diff.removed.length} removed · {diff.relabeled.length} relabeled
              {diff.counts ? ` · ${diff.counts.nodes_before} → ${diff.counts.nodes_after} nodes` : ""}
              {diff.computed_at ? ` · ${timeAgo(diff.computed_at)}` : ""}
            </span>
            <span className="kb-diff-toggle">{diffOpen ? "▾" : "▸"}</span>
          </div>
          {diffOpen && (
            <div className="kb-diff-body">
              {diff.added.length > 0 && (
                <div>
                  <strong>New nodes ({diff.added.length})</strong>
                  <ul>
                    {diff.added.slice(0, 8).map((n) => (
                      <li key={n.id}>
                        <button className="link-btn" onClick={() => onNodeClick?.(n.label || n.id)}>
                          {n.label || n.id}
                        </button>
                        {n.source_file && <span className="muted-note"> · {n.source_file}</span>}
                      </li>
                    ))}
                    {diff.added.length > 8 && <li className="muted-note">… and {diff.added.length - 8} more</li>}
                  </ul>
                </div>
              )}
              {diff.removed.length > 0 && (
                <div>
                  <strong>Removed ({diff.removed.length})</strong>
                  <ul>
                    {diff.removed.slice(0, 8).map((n) => (
                      <li key={n.id}>{n.label || n.id}{n.source_file && <span className="muted-note"> · {n.source_file}</span>}</li>
                    ))}
                    {diff.removed.length > 8 && <li className="muted-note">… and {diff.removed.length - 8} more</li>}
                  </ul>
                  <small className="muted-note">
                    Any corrections targeting removed nodes will show as orphaned and can be re-attached or deleted.
                  </small>
                </div>
              )}
              {diff.relabeled.length > 0 && (
                <div>
                  <strong>Relabeled ({diff.relabeled.length})</strong>
                  <ul>
                    {diff.relabeled.slice(0, 8).map((r) => (
                      <li key={r.id}>
                        <span className="muted-note">{r.old}</span> → <strong>{r.new}</strong>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      ) : null}

      <div className="refine-kb-filters">
        {(["all", "correction", "addition", "attestation", "dissent"] as const).map((k) => (
          <button
            key={k}
            className={`filter-chip ${filterKind === k ? "active" : ""}`}
            onClick={() => setFilterKind(k as typeof filterKind)}
            title={k === "all" ? "Everything" : KIND_TAGLINE[k]}
          >
            {k === "all" ? "All" : (
              <>
                <span style={{ marginRight: 4 }}>{KIND_ICON[k]}</span>
                {KIND_LABEL[k]}
              </>
            )} <span className="filter-count">{counts[k] ?? 0}</span>
          </button>
        ))}
        <input
          className="text-input small"
          placeholder="Search…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ marginLeft: "auto", maxWidth: 280 }}
        />
      </div>

      {error && <div className="pb-run-error">{error}</div>}

      {filtered.length === 0 && (
        <div className="empty-state">
          <h3>Nothing here yet</h3>
          <p>
            Tell the KB something it doesn't know — a fact you'd correct, add, confirm, or doubt. Your input
            shows up in every answer that touches the related topic, with your name attached.
          </p>
          <p className="muted-note">
            Tip: inside any conversation, click <em>💡 save as KB fact</em> on an assistant answer to attach a
            refinement to the source nodes for you.
          </p>
        </div>
      )}

      <ul className="refine-kb-list">
        {filtered.map((c) => (
          <li key={c.id} className={`refine-row refine-row-${c.kind}`}>
            <div className="refine-row-head">
              <span className={`refine-kind refine-kind-${c.kind}`} title={KIND_TAGLINE[c.kind]}>
                <span style={{ marginRight: 4 }}>{KIND_ICON[c.kind]}</span>{KIND_LABEL[c.kind]}
              </span>
              {c.target_node_id && (
                <button
                  className="link-btn refine-target"
                  onClick={() => onNodeClick?.(c.target_node_id!)}
                  title="Open node in graph"
                >
                  → {c.target_node_id}
                </button>
              )}
              <span className={`conf-badge conf-${c.confidence}`}>{c.confidence}</span>
              <span className={`src-badge src-${c.source_type}`}>{SOURCE_TYPE_LABEL[c.source_type]}</span>
              <span className="muted-note" style={{ marginLeft: "auto" }}>
                {timeAgo(c.updated_at)}
              </span>
              <button className="btn-secondary small" onClick={() => startEdit(c)}>Edit</button>
              <button className="btn-secondary small" onClick={() => remove(c)}>Delete</button>
            </div>
            <div className="refine-row-body">
              {c.kind === "correction" && (
                <>
                  {c.original_summary && (
                    <div className="refine-original">
                      <span className="refine-tag">was</span> {c.original_summary}
                    </div>
                  )}
                  <div className="refine-new">
                    <span className="refine-tag">now</span> {c.new_summary}
                  </div>
                </>
              )}
              {c.kind === "addition" && (
                <div className="refine-new"><span className="refine-tag">new</span> {c.new_summary}</div>
              )}
              {c.kind === "attestation" && (
                <div className="refine-original">
                  <span className="refine-tag">verified</span> {c.original_summary || "(as stated in graph)"}
                </div>
              )}
              {c.kind === "dissent" && (
                <>
                  <div className="refine-original">
                    <span className="refine-tag">graph says</span> {c.original_summary || "(see node)"}
                  </div>
                  <div className="refine-new"><span className="refine-tag">dissent</span> {c.new_summary || c.reason}</div>
                </>
              )}
              <div className="refine-meta">
                {c.author && <span><strong>{c.author}</strong>{c.author_basis ? ` — ${c.author_basis}` : ""}</span>}
                {c.reason && <span className="refine-reason">“{c.reason}”</span>}
                {c.evidence_url && (
                  <a href={c.evidence_url} target="_blank" rel="noreferrer" className="refine-evidence">
                    evidence ↗
                  </a>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
        </>
      )}
    </div>
  );
}

function Composer({
  state, onChange, onCancel, onSave, error,
}: {
  state: ComposerState;
  onChange: (next: ComposerState) => void;
  onCancel: () => void;
  onSave: () => void;
  error: string | null;
}) {
  const d = state.draft;
  const set = (patch: Partial<KBCorrection>) =>
    onChange({ ...state, draft: { ...d, ...patch } });
  const setKind = (k: KBRefinementKind) => set({ kind: k });

  const [detailsOpen, setDetailsOpen] = useState(false);
  const kind = (d.kind || "correction") as KBRefinementKind;
  const needsTarget = kind !== "addition";

  // Which field captures the user's text differs by kind. For Confirm
  // (attestation) we capture into `original_summary` (the thing being
  // verified); for everything else, into `new_summary` (the new claim).
  const contentValue = kind === "attestation"
    ? (d.original_summary ?? "")
    : (d.new_summary ?? "");
  const setContent = (v: string) =>
    set(kind === "attestation" ? { original_summary: v } : { new_summary: v });

  const canSave =
    (!needsTarget || !!d.target_node_id) &&
    (contentValue.trim().length > 0);

  const headline = state.mode === "create" ? "Tell the KB something" : "Edit refinement";

  return (
    <div className="refine-kb refine-kb-composer">
      <header className="refine-kb-head">
        <div>
          <button className="btn-secondary small" onClick={onCancel}>← Back</button>
          <h2 style={{ marginTop: 8 }}>{headline}</h2>
          <p className="muted-note" style={{ marginTop: 4 }}>
            Your input becomes a layer on top of the KB — answers see it without changing the underlying graph,
            and you can undo any time.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button className="btn-secondary small" onClick={onCancel}>Cancel</button>
          <button className="btn-primary small" onClick={onSave} disabled={!canSave}>
            Save
          </button>
        </div>
      </header>

      <div className="composer-step">
        <div className="composer-step-label">1 · What do you want to do?</div>
        <div className="kind-tiles">
          {(["correction", "addition", "attestation", "dissent"] as KBRefinementKind[]).map((k) => (
            <button
              key={k}
              type="button"
              className={`kind-tile ${kind === k ? "active" : ""} kind-tile-${k}`}
              onClick={() => setKind(k)}
            >
              <span className="kind-tile-icon">{KIND_ICON[k]}</span>
              <span className="kind-tile-action">{KIND_ACTION[k]}</span>
              <span className="kind-tile-tagline">{KIND_TAGLINE[k]}</span>
            </button>
          ))}
        </div>
      </div>

      {needsTarget && (
        <div className="composer-step">
          <div className="composer-step-label">
            2 · {kind === "correction" && "Which fact are you fixing?"}
            {kind === "attestation" && "Which fact are you confirming?"}
            {kind === "dissent" && "Which fact are you doubting?"}
          </div>
          <NodePicker
            picked={state.pickedNode}
            onPick={(n) => {
              onChange({ ...state, pickedNode: n });
              set({
                target_node_id: n?.id ?? null,
                // Seed the "what the graph said" from the node label so the
                // user doesn't have to retype it.
                original_summary: n?.label ?? d.original_summary ?? "",
              });
            }}
          />
        </div>
      )}

      <div className="composer-step">
        <div className="composer-step-label">
          {needsTarget ? "3" : "2"} · {KIND_PROMPT[kind]}
        </div>
        <textarea
          className="composer-content"
          rows={4}
          value={contentValue}
          onChange={(e) => setContent(e.target.value)}
          placeholder={KIND_PLACEHOLDER[kind]}
          autoFocus
        />
      </div>

      <details
        className="composer-details"
        open={detailsOpen}
        onToggle={(e) => setDetailsOpen((e.target as HTMLDetailsElement).open)}
      >
        <summary>Optional: who said this, how sure, evidence</summary>
        <div className="composer-details-grid">
          <label className="modal-field">
            <span>Your name</span>
            <input
              type="text"
              value={d.author ?? ""}
              onChange={(e) => set({ author: e.target.value })}
              placeholder="e.g. Filip"
            />
            <small className="muted-note">Saved for next time.</small>
          </label>
          <label className="modal-field">
            <span>Your basis (why this matters)</span>
            <input
              type="text"
              value={d.author_basis ?? ""}
              onChange={(e) => set({ author_basis: e.target.value })}
              placeholder="e.g. 10 yrs at Appfire, Marketplace lead"
            />
          </label>
          <label className="modal-field">
            <span>How sure are you?</span>
            <select
              value={d.confidence ?? "medium"}
              onChange={(e) => set({ confidence: e.target.value as KBConfidence })}
            >
              <option value="high">Very sure</option>
              <option value="medium">Reasonably sure</option>
              <option value="low">Not very sure</option>
            </select>
          </label>
          <label className="modal-field">
            <span>Evidence link</span>
            <input
              type="url"
              value={d.evidence_url ?? ""}
              onChange={(e) => set({ evidence_url: e.target.value || null })}
              placeholder="https://…"
            />
          </label>
          <label className="modal-field" style={{ gridColumn: "1 / -1" }}>
            <span>Anything else worth noting?</span>
            <textarea
              rows={2}
              value={d.reason ?? ""}
              onChange={(e) => set({ reason: e.target.value })}
              placeholder="Context, when you learned it, who else knows…"
            />
          </label>
        </div>
      </details>

      {error && <div className="pb-run-error" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  );
}

function NodePicker({
  picked,
  onPick,
}: {
  picked: KBNodeMatch | null;
  onPick: (n: KBNodeMatch | null) => void;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<KBNodeMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(async () => {
      if (q.trim().length < 2) {
        setResults([]);
        return;
      }
      setLoading(true);
      try {
        const r = await api.searchKBNodes(q, { limit: 25 });
        setResults(r.results);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  if (picked && !open) {
    return (
      <div className="node-picker-selected">
        <div>
          <div>
            <strong>{picked.label}</strong>
            {picked.extracted_at && (
              <span className="freshness-badge" title={`Extracted ${new Date(picked.extracted_at * 1000).toLocaleString()}`}>
                extracted {timeAgo(picked.extracted_at)}
              </span>
            )}
          </div>
          <div className="muted-note">
            <code style={{ fontSize: 11 }}>{picked.id}</code>
            {picked.source_file ? ` · ${picked.source_file}` : ""}
            {picked.community_label ? ` · ${picked.community_label}` : ""}
          </div>
        </div>
        <button className="btn-secondary small" onClick={() => { setOpen(true); setQ(""); }}>Change</button>
      </div>
    );
  }

  return (
    <div className="node-picker">
      <input
        className="text-input"
        autoFocus
        placeholder="Search nodes by label…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {loading && <div className="muted-note" style={{ padding: 6 }}>Searching…</div>}
      {!loading && q.trim().length >= 2 && results.length === 0 && (
        <div className="muted-note" style={{ padding: 6 }}>No matches.</div>
      )}
      {results.length > 0 && (
        <ul className="node-picker-results">
          {results.map((r) => (
            <li key={r.id}>
              <button
                className="node-picker-row"
                onClick={() => { onPick(r); setOpen(false); }}
              >
                <span>{r.label}</span>
                <span className="muted-note" style={{ fontSize: 11 }}>
                  {r.community_label ?? r.source_file ?? r.id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Browse every fact the KB currently believes. When a correction exists for
// a node, the corrected text becomes the row's headline — the LLM sees this
// view, and so does the user. The only action per row is Edit, because this
// page is for shaping the present, not auditing history.
function FactsList({
  corrections,
  onActOnNode,
  onEditExisting,
  onOpenNode,
}: {
  corrections: KBCorrection[];
  onActOnNode: (n: KBNodeMatch, kind: KBRefinementKind) => void;
  onEditExisting: (c: KBCorrection) => void;
  onOpenNode: (label: string) => void;
}) {
  const PAGE = 50;
  const [q, setQ] = useState("");
  const [community, setCommunity] = useState<string>("");
  const [sourceFile, setSourceFile] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{
    results: KBNodeMatch[];
    total: number;
    communities: Array<{ label: string; count: number }>;
    source_files: Array<{ label: string; count: number }>;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [refinedOnly, setRefinedOnly] = useState(false);
  const [staleOnly, setStaleOnly] = useState(false);
  // Code-derived facts (functions, classes, modules) don't fit the fix/add/
  // confirm/doubt gesture. Hidden by default; the user can toggle them on.
  const [includeCode, setIncludeCode] = useState(false);

  // Index corrections by target so we can swap in the corrected value at
  // render time without an extra fetch.
  const correctionByTarget = useMemo(() => {
    const idx: Record<string, KBCorrection> = {};
    for (const c of corrections) {
      if (!c.target_node_id) continue;
      // Corrections win as the displayed truth. Attestations and dissents
      // exist but don't override the headline — see render below.
      if (c.kind === "correction") {
        const existing = idx[c.target_node_id];
        if (!existing || (c.updated_at || 0) > (existing.updated_at || 0)) {
          idx[c.target_node_id] = c;
        }
      }
    }
    return idx;
  }, [corrections]);

  // Reset offset whenever a filter changes.
  useEffect(() => { setOffset(0); }, [q, community, sourceFile, refinedOnly, staleOnly, includeCode]);

  useEffect(() => {
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api.searchKBNodes(q, {
          limit: PAGE,
          offset,
          community: community || null,
          source_file: sourceFile || null,
          include_code: includeCode,
        });
        setData(r);
      } finally {
        setLoading(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [q, community, sourceFile, offset, includeCode]);

  const visible = useMemo(() => {
    if (!data) return [];
    let rows = data.results;
    if (refinedOnly) {
      rows = rows.filter((r) => !!correctionByTarget[r.id]);
    }
    if (staleOnly) {
      const sixMonthsAgo = Date.now() / 1000 - 60 * 60 * 24 * 30 * 6;
      rows = rows.filter((r) => r.extracted_at != null && r.extracted_at < sixMonthsAgo);
    }
    return rows;
  }, [data, refinedOnly, staleOnly, correctionByTarget]);

  const total = data?.total ?? 0;
  const start = Math.min(offset + 1, total);
  const end = Math.min(offset + PAGE, total);

  return (
    <div className="facts-list">
      <p className="muted-note" style={{ marginBottom: 10 }}>
        Every fact the KB currently believes — what the LLM sees when it answers. Edit a row to refine
        it. Removing your edit restores what the graph extraction said.
      </p>

      <div className="facts-toolbar">
        <input
          className="text-input small"
          placeholder="Search facts…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 220 }}
        />
        {data && data.communities.length > 0 && (
          <select value={community} onChange={(e) => setCommunity(e.target.value)} className="facts-select">
            <option value="">All topics</option>
            {data.communities.map((c) => (
              <option key={c.label} value={c.label}>{c.label} ({c.count})</option>
            ))}
          </select>
        )}
        {data && data.source_files.length > 0 && (
          <select value={sourceFile} onChange={(e) => setSourceFile(e.target.value)} className="facts-select">
            <option value="">All sources</option>
            {data.source_files.slice(0, 60).map((s) => (
              <option key={s.label} value={s.label}>{s.label} ({s.count})</option>
            ))}
          </select>
        )}
        <label className="facts-chip">
          <input type="checkbox" checked={refinedOnly} onChange={(e) => setRefinedOnly(e.target.checked)} />
          Edited only
        </label>
        <label className="facts-chip">
          <input type="checkbox" checked={staleOnly} onChange={(e) => setStaleOnly(e.target.checked)} title="Extracted more than 6 months ago" />
          Stale (≥6mo)
        </label>
        <label className="facts-chip" title="Code-derived facts (functions, classes) don't fit Fix/Add/Confirm/Doubt. Toggle on if you want to annotate them anyway.">
          <input type="checkbox" checked={includeCode} onChange={(e) => setIncludeCode(e.target.checked)} />
          Show code nodes
        </label>
        <span className="muted-note" style={{ marginLeft: "auto" }}>
          {loading ? "Loading…" : total > 0 ? `${start}–${end} of ${total}` : "0 facts"}
        </span>
      </div>

      {!loading && visible.length === 0 && (
        <div className="empty-state">
          <h3>{total === 0 ? "No facts in this workspace yet" : "Nothing matches"}</h3>
          <p className="muted-note">
            {total === 0
              ? "Upload a document or repo to build a graph; its facts will appear here."
              : "Try clearing filters or widening your search."}
          </p>
        </div>
      )}

      <ul className="facts-list-rows">
        {visible.map((n) => {
          const correction = correctionByTarget[n.id];
          const isEdited = !!correction;
          // What the KB believes RIGHT NOW: the corrected value if present,
          // otherwise the graph extraction. This is what answers will cite.
          const currentTruth = isEdited && correction.new_summary ? correction.new_summary : n.label;
          return (
            <li key={n.id} className={`facts-row ${isEdited ? "facts-row-refined" : ""}`}>
              <div className="facts-row-main">
                <button className="facts-row-label" onClick={() => onOpenNode(n.label)} title="Open in graph">
                  {currentTruth}
                  {isEdited && (
                    <span className="facts-edited-pill" title="You've edited this fact. Click Edit to view or change.">
                      edited
                    </span>
                  )}
                </button>
                <div className="facts-row-meta">
                  {n.entity_type && (
                    <span
                      className="facts-meta-pill entity-pill"
                      title={`Deterministically typed as ${n.entity_type} (no LLM call)`}
                    >
                      {n.entity_type === "person" ? "👤" : n.entity_type === "company" ? "🏢" : n.entity_type === "organization" ? "🏛" : "📦"} {n.entity_type}
                    </span>
                  )}
                  {n.community_label && <span className="facts-meta-pill">{n.community_label}</span>}
                  {n.source_file && <span className="muted-note">📄 {n.source_file}</span>}
                  {n.extracted_at && (
                    <span className="freshness-badge" title={`Extracted ${new Date(n.extracted_at * 1000).toLocaleString()}`}>
                      {timeAgo(n.extracted_at)}
                    </span>
                  )}
                </div>
              </div>
              <div className="facts-row-actions">
                <button
                  className="btn-primary small"
                  onClick={() => isEdited ? onEditExisting(correction) : onActOnNode(n, "correction")}
                  title={isEdited ? "Edit your refinement" : "Refine this fact"}
                >
                  ✎ Edit
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {total > PAGE && (
        <div className="facts-pager">
          <button
            className="btn-secondary small"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            ← Prev
          </button>
          <span className="muted-note">Page {Math.floor(offset / PAGE) + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
          <button
            className="btn-secondary small"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function timeAgo(ts: number): string {
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
