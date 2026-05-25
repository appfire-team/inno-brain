import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type Artifact,
  type ArtifactComment,
  type ArtifactHighlight,
  type ArtifactQA,
  type ArtifactSimplified,
  type PlaybookRun,
} from "../api";
import { InlineMarkdown, MarkdownView } from "./MarkdownView";
import { wordDiff } from "../utils/diff";

const DOC_LEVEL = "__document__";

type Props = {
  runId: string;
  onClose: () => void;
  onRerun?: (run: PlaybookRun) => void;
  artifactTypes: Record<string, string>;
};

export function PlaybookRunView({ runId, onClose, onRerun, artifactTypes }: Props) {
  const [run, setRun] = useState<PlaybookRun | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [actionPending, setActionPending] = useState<null | "cancel" | "resume">(null);
  const [exportFlash, setExportFlash] = useState<string | null>(null);

  // Review state for the artifact.
  const [reviewOpen, setReviewOpen] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [commentSection, setCommentSection] = useState<string>(DOC_LEVEL);
  const [postingComment, setPostingComment] = useState(false);
  const [refining, setRefining] = useState(false);
  const [viewVersion, setViewVersion] = useState<number | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [readingMode, setReadingMode] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [activeSection, setActiveSection] = useState<string | null>(null);

  // Simplified-language toggle.
  const [simplifyMode, setSimplifyMode] = useState(false);
  const [simplified, setSimplified] = useState<ArtifactSimplified | null>(null);
  const [simplifying, setSimplifying] = useState(false);
  const [simplifyError, setSimplifyError] = useState<string | null>(null);

  // Follow-up Q&A on the artifact.
  const [qaHistory, setQaHistory] = useState<ArtifactQA[]>([]);
  const [qaQuestion, setQaQuestion] = useState("");
  const [askPending, setAskPending] = useState(false);
  const [qaError, setQaError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.getPlaybookRun(runId);
      setRun(r);
      if (r.final_artifact_id) {
        try {
          const a = await api.getArtifact(r.final_artifact_id);
          setArtifact(a);
          setQaHistory(a.qa_history ?? []);
          setSimplified(a.simplified ?? null);
        } catch {
          // race: the artifact may briefly not exist yet
        }
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [runId]);

  const toggleSimplify = useCallback(async () => {
    if (!artifact) return;
    // If we already have a fresh cached version, just flip the view.
    if (
      simplified?.body &&
      simplified.source_updated_at === artifact.updated_at &&
      !simplifyMode
    ) {
      setSimplifyMode(true);
      return;
    }
    if (simplifyMode) {
      setSimplifyMode(false);
      return;
    }
    setSimplifying(true);
    setSimplifyError(null);
    try {
      const result = await api.simplifyArtifact(artifact.id);
      setSimplified(result);
      setSimplifyMode(true);
    } catch (e) {
      setSimplifyError((e as Error).message);
    } finally {
      setSimplifying(false);
    }
  }, [artifact, simplified, simplifyMode]);

  const submitQuestion = useCallback(async () => {
    if (!artifact) return;
    const q = qaQuestion.trim();
    if (!q) return;
    setAskPending(true);
    setQaError(null);
    try {
      const entry = await api.askArtifact(artifact.id, q);
      setQaHistory((prev) => [...prev, entry]);
      setQaQuestion("");
    } catch (e) {
      setQaError((e as Error).message);
    } finally {
      setAskPending(false);
    }
  }, [artifact, qaQuestion]);

  const removeQuestion = useCallback(
    async (qaId: string) => {
      if (!artifact) return;
      try {
        await api.deleteArtifactQA(artifact.id, qaId);
        setQaHistory((prev) => prev.filter((q) => q.id !== qaId));
      } catch (e) {
        setQaError((e as Error).message);
      }
    },
    [artifact],
  );

  useEffect(() => { refresh(); }, [refresh]);

  // Escape exits reading mode.
  useEffect(() => {
    if (!readingMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReadingMode(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readingMode]);

  // Poll while the run is still in flight.
  useEffect(() => {
    if (!run) return;
    if (run.status !== "running" && run.status !== "queued") return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [run, refresh]);

  const cancelRun = async () => {
    if (!run || actionPending) return;
    if (!confirm("Cancel this run? The current step will finish, then the run stops.")) return;
    setActionPending("cancel");
    try {
      const next = await api.cancelPlaybookRun(runId);
      setRun(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setActionPending(null);
    }
  };

  const resumeRun = async () => {
    if (!run || actionPending) return;
    setActionPending("resume");
    try {
      const next = await api.resumePlaybookRun(runId);
      setRun(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setActionPending(null);
    }
  };

  // Pick the version body to display — defaults to current, falls back to top-level fields.
  const displayedVersion = useMemo(() => {
    if (!artifact) return null;
    const target = viewVersion ?? artifact.current_version;
    return artifact.versions.find((v) => v.v === target) ?? null;
  }, [artifact, viewVersion]);

  const displayedTldr = displayedVersion?.tldr ?? artifact?.tldr ?? "";
  const displayedSections = displayedVersion?.sections ?? artifact?.sections ?? {};
  const displayedHighlights = displayedVersion?.highlights ?? artifact?.highlights ?? [];
  const displayedMarkdown = displayedVersion?.raw_markdown ?? artifact?.raw_markdown ?? "";
  const isOldVersion =
    artifact != null && viewVersion != null && viewVersion !== artifact.current_version;

  const openCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    if (!artifact) return counts;
    for (const c of artifact.comments) {
      if (c.status !== "open") continue;
      const key = c.section ?? DOC_LEVEL;
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return counts;
  }, [artifact]);

  const totalOpen = artifact ? artifact.comments.filter((c) => c.status === "open").length : 0;
  const totalAddressed = artifact
    ? artifact.comments.filter((c) => c.status === "addressed").length
    : 0;

  // For the diff: pick the version immediately before the one being displayed.
  const priorVersion = useMemo(() => {
    if (!artifact || !displayedVersion) return null;
    if (displayedVersion.v <= 1) return null;
    return artifact.versions.find((v) => v.v === displayedVersion.v - 1) ?? null;
  }, [artifact, displayedVersion]);

  const sectionChanged = useMemo(() => {
    const out: Record<string, boolean> = {};
    if (!priorVersion || !displayedVersion) return out;
    for (const k of Object.keys(displayedSections)) {
      out[k] = (priorVersion.sections[k] ?? "") !== (displayedSections[k] ?? "");
    }
    return out;
  }, [priorVersion, displayedVersion, displayedSections]);

  const tldrChanged = priorVersion ? priorVersion.tldr !== displayedTldr : false;
  const hasPriorVersion = priorVersion != null;

  const copyArtifactMarkdown = async () => {
    if (!artifact) return;
    try {
      await navigator.clipboard.writeText(displayedMarkdown);
      setExportFlash("Copied as Markdown");
      setTimeout(() => setExportFlash(null), 1800);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const submitComment = async () => {
    if (!artifact || !commentText.trim() || postingComment) return;
    setPostingComment(true);
    try {
      const c = await api.addArtifactComment(artifact.id, {
        text: commentText.trim(),
        section: commentSection === DOC_LEVEL ? null : commentSection,
      });
      setArtifact({ ...artifact, comments: [...artifact.comments, c] });
      setCommentText("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPostingComment(false);
    }
  };

  const changeCommentStatus = async (commentId: string, status: ArtifactComment["status"]) => {
    if (!artifact) return;
    try {
      const updated = await api.updateArtifactComment(artifact.id, commentId, { status });
      setArtifact({
        ...artifact,
        comments: artifact.comments.map((c) => (c.id === commentId ? updated : c)),
      });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const refineWithComments = async () => {
    if (!artifact || refining) return;
    setRefining(true);
    try {
      const updated = await api.refineArtifact(artifact.id);
      setArtifact(updated);
      setViewVersion(null); // jump to the new current version
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefining(false);
    }
  };

  const sectionKeys = useMemo(() => Object.keys(displayedSections), [displayedSections]);

  // Highlight whichever section is closest to the top of the viewport. The
  // observer is scoped to actual section elements, so it just works for any
  // historical run that already had sections — no data migration.
  useEffect(() => {
    if (sectionKeys.length === 0) return;
    const ids = sectionKeys.map(sectionAnchorId);
    const targets = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el != null);
    if (targets.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) {
          const id = (visible[0].target as HTMLElement).id;
          const key = sectionKeys.find((k) => sectionAnchorId(k) === id);
          if (key) setActiveSection(key);
        }
      },
      { rootMargin: "-10% 0px -70% 0px", threshold: 0 },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [sectionKeys, artifact?.current_version, viewVersion]);

  const jumpToSection = (key: string) => {
    // Un-collapse before scrolling so the section actually has somewhere to land.
    setCollapsedSections((s) => ({ ...s, [key]: false }));
    requestAnimationFrame(() => {
      const el = document.getElementById(sectionAnchorId(key));
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const toggleSection = (key: string) =>
    setCollapsedSections((s) => ({ ...s, [key]: !s[key] }));

  const allCollapsed = sectionKeys.length > 0 && sectionKeys.every((k) => collapsedSections[k]);
  const setAllCollapsed = (collapsed: boolean) => {
    const next: Record<string, boolean> = {};
    for (const k of sectionKeys) next[k] = collapsed;
    setCollapsedSections(next);
  };

  const downloadArtifactMarkdown = () => {
    if (!artifact) return;
    const safeName = (artifact.title || "artifact").replace(/[^a-z0-9-_]+/gi, "-").slice(0, 60);
    const blob = new Blob([displayedMarkdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeName}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!run && !error) {
    return <div className="pb-run-loading"><span className="spinner" /> Loading run…</div>;
  }
  if (error || !run) {
    return (
      <div className="pb-run-loading">
        <div className="error-text">{error ?? "Run not found"}</div>
        <button className="btn-secondary small" onClick={onClose} style={{ marginTop: 12 }}>← Back to Playbooks</button>
      </div>
    );
  }

  const elapsedS = (run.finished_at ?? Date.now() / 1000) - run.started_at;
  const isInFlight = run.status === "running" || run.status === "queued";

  return (
    <div className={`pb-run ${readingMode ? "pb-run-reading" : ""}`}>
      {readingMode && (
        <button
          className="pb-reading-exit"
          onClick={() => setReadingMode(false)}
          title="Exit reading view (Esc)"
        >
          ✕ Exit reading view
        </button>
      )}
      <header className="pb-run-head">
        <button className="btn-secondary small" onClick={onClose}>← Back to Playbooks</button>
        <h2>{run.playbook_label}</h2>
        <span className={`pb-status-badge pb-status-${run.status}`}>{run.status.toUpperCase()}</span>
        <div className="pb-run-actions">
          {isInFlight && (
            <button
              className="btn-secondary small"
              onClick={cancelRun}
              disabled={actionPending === "cancel" || run.cancel_requested}
              title="Cancel this run after the current step finishes"
            >
              {run.cancel_requested ? "Cancelling…" : actionPending === "cancel" ? <span className="spinner" /> : "Cancel"}
            </button>
          )}
          {(run.status === "failed" || run.status === "cancelled") && (
            <button
              className="btn-primary small"
              onClick={resumeRun}
              disabled={actionPending === "resume"}
              title="Pick up from the first non-complete step. Prior steps' outputs are preserved."
            >
              {actionPending === "resume" ? <><span className="spinner" /> Resuming…</> : "↻ Resume"}
            </button>
          )}
          {run.status === "complete" && onRerun && (
            <button
              className="btn-secondary small"
              onClick={() => onRerun(run)}
              title="Open the kickoff form pre-filled with this run's inputs"
            >
              ⤴ Rerun
            </button>
          )}
        </div>
      </header>

      <div className="pb-run-scenario-box">
        <div className="muted-note" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.04 }}>Scenario</div>
        <div>{run.user_inputs.scenario}</div>
      </div>

      <div className="pb-run-meta">
        <span>{run.steps.length} steps</span>
        <span>{elapsedS.toFixed(0)}s elapsed</span>
        <span>{run.total_tokens.input.toLocaleString()} in · {run.total_tokens.output.toLocaleString()} out</span>
        {run.user_inputs.horizon && <span>⏳ horizon: {horizonLabel(run.user_inputs.horizon)}</span>}
        {run.user_inputs.synth_inference_strategy && run.user_inputs.synth_inference_strategy !== "none" && (
          <span>✦ synth: {run.user_inputs.synth_inference_strategy}</span>
        )}
        {run.user_inputs.fact_check && <span>✅ fact-check</span>}
        {run.user_inputs.answer_model && <span>⚙ {modelShortLabel(run.user_inputs.answer_model)}</span>}
        {run.user_inputs.rubric_id && <span>📐 rubric</span>}
        {run.user_inputs.source_artifact_id && <span>↳ built on prior artifact</span>}
        {run.user_inputs.web_grounding && <span>🌐 web grounding</span>}
      </div>

      <ol className="pb-timeline">
        {run.steps.map((s, idx) => {
          const expand = expanded[s.id] ?? (s.status === "running" || s.status === "failed");
          return (
            <li key={s.id} className={`pb-step pb-step-${s.status}`}>
              <button
                className="pb-step-head"
                onClick={() => setExpanded((m) => ({ ...m, [s.id]: !expand }))}
              >
                <span className="pb-step-marker">
                  {s.status === "complete" && "✓"}
                  {s.status === "running" && <span className="spinner" />}
                  {s.status === "pending" && <span className="pb-step-dot">{idx + 1}</span>}
                  {s.status === "failed" && "✕"}
                </span>
                <span className="pb-step-label">{s.label}</span>
                <span className="pb-step-type-tag">{stepTypeLabel(s.type)}</span>
                {s.status === "complete" && (
                  <span className="pb-step-tokens">
                    {s.tokens.input.toLocaleString()} / {s.tokens.output.toLocaleString()} tok
                  </span>
                )}
              </button>
              {expand && s.output && (
                <div className="pb-step-body">
                  <MarkdownView className="pb-step-md">{s.output}</MarkdownView>
                  <CollapsibleSources label="Web sources" sources={s.web_sources} />
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {run.error && (
        <div className="pb-run-error">
          <strong>Playbook failed.</strong> {run.error}
        </div>
      )}

      {artifact && run.status === "complete" && (
        <div className="pb-final-brief">
          <header className="pb-final-brief-head">
            <div>
              <span className="muted-note" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.05 }}>
                {artifactTypes[artifact.type] ?? artifact.type}
              </span>
              <h2>{artifact.title}</h2>
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
                  title="Switch between versions"
                >
                  {[...artifact.versions].reverse().map((v) => (
                    <option key={v.v} value={v.v}>
                      v{v.v}{v.v === artifact.current_version ? " · current" : ""}
                    </option>
                  ))}
                </select>
              )}
              {hasPriorVersion && (
                <button
                  className={`btn-secondary small ${showDiff ? "active" : ""}`}
                  onClick={() => setShowDiff((x) => !x)}
                  title={showDiff ? "Hide inline changes" : `Show inline changes vs v${priorVersion!.v}`}
                >
                  {showDiff ? "✓ Showing changes" : `↔ Show changes vs v${priorVersion!.v}`}
                </button>
              )}
              <button
                className="btn-secondary small"
                onClick={() => setReviewOpen((x) => !x)}
                title="Show review comments"
              >
                💬 Comments
                {totalOpen > 0 && <strong style={{ marginLeft: 4 }}>({totalOpen} open)</strong>}
              </button>
              <button
                className="btn-secondary small"
                onClick={() => setReadingMode(true)}
                title="Enter reading view — hides everything except the brief (Esc to exit)"
              >
                📖 Reading view
              </button>
              <button
                className={`btn-secondary small ${simplifyMode ? "active" : ""}`}
                onClick={toggleSimplify}
                disabled={simplifying}
                title={
                  simplifyMode
                    ? "Switch back to the detailed brief"
                    : "Rewrite this brief in plain language (no jargon, every term defined)"
                }
              >
                {simplifying ? (
                  <>
                    <span className="spinner" /> Simplifying…
                  </>
                ) : simplifyMode ? (
                  "✓ Plain language"
                ) : (
                  "🧒 Explain in plain language"
                )}
              </button>
              <button className="btn-secondary small" onClick={copyArtifactMarkdown}>
                {exportFlash === "Copied as Markdown" ? "✓ Copied" : "Copy as Markdown"}
              </button>
              <button className="btn-secondary small" onClick={downloadArtifactMarkdown}>
                ⇩ Download .md
              </button>
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

          {hasPriorVersion && displayedVersion && (
            <div className="pb-changelog-ribbon">
              <strong>What changed in v{displayedVersion.v}:</strong>{" "}
              <span>{displayedVersion.summary || "(no changelog recorded)"}</span>
            </div>
          )}

          {reviewOpen && (
            <div className="pb-review-panel">
              <div className="pb-review-head">
                <strong>Review</strong>
                <span className="muted-note" style={{ marginLeft: 8 }}>
                  {totalOpen} open · {totalAddressed} addressed · {artifact.comments.length} total
                </span>
                <button
                  className="btn-primary small"
                  onClick={refineWithComments}
                  disabled={refining || totalOpen === 0 || isOldVersion}
                  title={
                    totalOpen === 0
                      ? "Add at least one open comment first"
                      : isOldVersion
                        ? "Switch to the current version before refining"
                        : "Apply all open comments via an LLM revision (creates a new version)"
                  }
                  style={{ marginLeft: "auto" }}
                >
                  {refining ? <><span className="spinner" /> Refining…</> : `↻ Refine with comments (${totalOpen})`}
                </button>
              </div>

              <div className="pb-review-composer">
                <select
                  value={commentSection}
                  onChange={(e) => setCommentSection(e.target.value)}
                >
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
                  disabled={!commentText.trim() || postingComment}
                >
                  {postingComment ? <span className="spinner" /> : "Add comment"}
                </button>
              </div>

              {artifact.comments.length === 0 ? (
                <div className="muted-note">No comments yet. Add one above.</div>
              ) : (
                <ul className="pb-comment-list">
                  {artifact.comments.map((c) => (
                    <li key={c.id} className={`pb-comment pb-comment-${c.status}`}>
                      <div className="pb-comment-meta">
                        <span className={`pb-comment-status pb-comment-status-${c.status}`}>
                          {c.status}
                        </span>
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
                  {showDiff && priorVersion ? (
                    <DiffText oldText={priorVersion.tldr} newText={displayedTldr} />
                  ) : (
                    <>
                      {displayedTldr}
                      {tldrChanged && !showDiff && (
                        <span className="pb-changed-badge" title="TL;DR changed since the prior version">changed</span>
                      )}
                    </>
                  )}
                </div>
                {displayedHighlights.length > 0 && (
                  <div className="pb-highlights">
                    {displayedHighlights.map((h: ArtifactHighlight, i) => (
                      <div key={i} className={`pb-highlight pb-highlight-${h.tone}`}>
                        <span className="pb-highlight-icon">{highlightIcon(h.tone)}</span>
                        <span className="pb-highlight-text">{h.text}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {simplifyMode && simplified?.body ? (
                <div className="pb-simplified-banner">
                  <strong>🧒 Plain-language rewrite.</strong>
                  <span className="muted-note" style={{ marginLeft: 6 }}>
                    Same brief, every term defined inline. Toggle off to see the
                    detailed version.
                  </span>
                </div>
              ) : null}
              {simplifyError && (
                <div className="pb-run-error">Simplify failed: {simplifyError}</div>
              )}
              {!showDiff && !simplifyMode && (
                <BriefSpotlight
                  artifactType={artifact.type}
                  sections={displayedSections}
                />
              )}
              {simplifyMode && simplified?.body ? (
                <div className="pb-simplified-body">
                  <MarkdownView>{simplified.body}</MarkdownView>
                </div>
              ) : (
              <div className="pb-sections">
                {Object.entries(displayedSections).map(([k, v], idx) => {
                  const n = openCounts[k] ?? 0;
                  const changed = sectionChanged[k];
                  const verdict = !showDiff ? extractVerdict(v || "", k) : null;
                  const { lead, rest } = !showDiff ? splitLeadSentence(v || "") : { lead: null, rest: v || "" };
                  const collapsed = !!collapsedSections[k];
                  return (
                    <section
                      key={k}
                      id={sectionAnchorId(k)}
                      className={collapsed ? "pb-section-collapsed" : ""}
                    >
                      <h3 onClick={() => toggleSection(k)} title={collapsed ? "Expand section" : "Collapse section"}>
                        <button
                          className="pb-section-toggle"
                          onClick={(e) => { e.stopPropagation(); toggleSection(k); }}
                          aria-label={collapsed ? "Expand" : "Collapse"}
                        >
                          {collapsed ? "▸" : "▾"}
                        </button>
                        <span className="pb-section-num">{idx + 1}.</span>{" "}
                        {(() => {
                          const icon = sectionIcon(k);
                          return icon ? <span className="pb-section-icon" aria-hidden>{icon}</span> : null;
                        })()}
                        {" "}{k}
                        {verdict && (
                          <span className={`pb-verdict-pill pb-verdict-${verdict.tone}`} title={verdict.label}>
                            {verdict.label}
                          </span>
                        )}
                        {changed && !showDiff && (
                          <span className="pb-changed-badge" title={`Changed since v${priorVersion!.v}`}>changed</span>
                        )}
                        {n > 0 && (
                          <button
                            className="link-btn pb-section-comment-badge"
                            onClick={(e) => {
                              e.stopPropagation();
                              setReviewOpen(true);
                              setCommentSection(k);
                            }}
                            title={`${n} open comment${n === 1 ? "" : "s"} on this section`}
                            style={{ marginLeft: 8, fontSize: 12 }}
                          >
                            💬 {n}
                          </button>
                        )}
                      </h3>
                      {collapsed ? (
                        <p className="pb-section-preview">
                          {previewOf(v || "")}
                        </p>
                      ) : (
                        <div className="pb-section-body">
                          {showDiff && priorVersion ? (
                            <DiffText oldText={priorVersion.sections[k] ?? ""} newText={v || ""} />
                          ) : lead ? (
                            <>
                              <p className="pb-lead-sentence">
                                <InlineMarkdown>{lead}</InlineMarkdown>
                              </p>
                              {rest && <MarkdownView>{rest}</MarkdownView>}
                            </>
                          ) : (
                            <MarkdownView>{v || "_(empty)_"}</MarkdownView>
                          )}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
              )}
              <CollapsibleSources label="Sources" sources={artifact.provenance?.web_sources ?? []} />

              {/* Follow-up Q&A panel. Anchored below the brief so the
                  reader has the full context above the input. */}
              <ArtifactQAPanel
                history={qaHistory}
                question={qaQuestion}
                pending={askPending}
                error={qaError}
                onChange={setQaQuestion}
                onSubmit={submitQuestion}
                onDelete={removeQuestion}
              />
            </div>
            {sectionKeys.length > 1 && !simplifyMode && (
              <PlaybookToc
                sections={Object.entries(displayedSections)}
                openCounts={openCounts}
                verdicts={!showDiff ? extractAllVerdicts(displayedSections) : {}}
                activeSection={activeSection}
                onJump={jumpToSection}
                allCollapsed={allCollapsed}
                onCollapseAll={() => setAllCollapsed(true)}
                onExpandAll={() => setAllCollapsed(false)}
              />
            )}
          </div>
        </div>
      )}

      {isInFlight && !artifact && (
        <div className="pb-waiting"><span className="spinner" /> Working — this view updates every few seconds. You can close the tab; the run continues server-side.</div>
      )}
    </div>
  );
}

// Surface load-bearing verdicts the LLM is instructed to emit (e.g. the
// technical feasibility step ends with one of these four tokens). When detected,
// we render a colored pill next to the section title — the verdict still
// appears in the prose for context.
type VerdictTone = "go" | "caution" | "warn" | "stop" | "build" | "buy" | "partner";

const VERDICTS: Record<string, { label: string; tone: VerdictTone }> = {
  "BUILDABLE-AS-SCOPED": { label: "Buildable as scoped", tone: "go" },
  "BUILDABLE-WITH-CUTS": { label: "Buildable with cuts", tone: "caution" },
  "BUILDABLE-AFTER-PREREQS": { label: "Buildable after prereqs", tone: "warn" },
  "NOT-BUILDABLE-IN-HORIZON": { label: "Not buildable in horizon", tone: "stop" },
};
const VERDICT_RE = new RegExp(`\\b(${Object.keys(VERDICTS).join("|")})\\b`);

// Build / Buy / Partner are only meaningful in a Recommendation context — they
// match common English words otherwise, so we gate detection on the section name.
const RECOMMENDATIONS: Record<string, { label: string; tone: VerdictTone }> = {
  BUILD: { label: "Build", tone: "build" },
  BUY: { label: "Buy", tone: "buy" },
  PARTNER: { label: "Partner", tone: "partner" },
};

function sectionAnchorId(name: string): string {
  return `section-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "x"}`;
}

function previewOf(text: string, max = 140): string {
  const flat = text.replace(/\s+/g, " ").replace(/[#*`>_-]+/g, "").trim();
  if (flat.length <= max) return flat;
  return flat.slice(0, max - 1).trimEnd() + "…";
}

function extractAllVerdicts(
  sections: Record<string, string>,
): Record<string, { label: string; tone: VerdictTone }> {
  const out: Record<string, { label: string; tone: VerdictTone }> = {};
  for (const [k, v] of Object.entries(sections)) {
    const verdict = extractVerdict(v || "", k);
    if (verdict) out[k] = verdict;
  }
  return out;
}

function PlaybookToc({
  sections,
  openCounts,
  verdicts,
  activeSection,
  onJump,
  allCollapsed,
  onCollapseAll,
  onExpandAll,
}: {
  sections: Array<[string, string]>;
  openCounts: Record<string, number>;
  verdicts: Record<string, { label: string; tone: VerdictTone }>;
  activeSection: string | null;
  onJump: (key: string) => void;
  allCollapsed: boolean;
  onCollapseAll: () => void;
  onExpandAll: () => void;
}) {
  return (
    <aside className="pb-toc" aria-label="Brief outline">
      <div className="pb-toc-head">
        <h4>On this page</h4>
        <button
          className="pb-toc-collapse-btn"
          onClick={allCollapsed ? onExpandAll : onCollapseAll}
          title={allCollapsed ? "Expand every section" : "Collapse every section"}
        >
          {allCollapsed ? "Expand all" : "Collapse all"}
        </button>
      </div>
      <ol className="pb-toc-list">
        {sections.map(([key], i) => {
          const verdict = verdicts[key];
          const n = openCounts[key] ?? 0;
          return (
            <li key={key} className="pb-toc-item">
              <span className="pb-toc-num">{i + 1}</span>
              <button
                className={`pb-toc-link ${activeSection === key ? "active" : ""}`}
                onClick={() => onJump(key)}
                title={key}
              >
                {key}
              </button>
              {verdict && (
                <span className={`pb-toc-pill tone-${verdict.tone}`} title={verdict.label}>
                  {verdict.label}
                </span>
              )}
              {n > 0 && <span className="pb-toc-comment" title={`${n} open comments`}>{n}💬</span>}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function extractVerdict(text: string, sectionName: string): { label: string; tone: VerdictTone } | null {
  if (!text) return null;
  // 1. Explicit feasibility-style tokens — precise, all-caps, safe to scan anywhere.
  const m = text.match(VERDICT_RE);
  if (m) return VERDICTS[m[1]] ?? null;

  // 2. Build/Buy/Partner — only for Recommendation-shaped sections, only in the
  //    first sentence-ish window so we don't catch e.g. "Build" mentioned later.
  if (/recommendation/i.test(sectionName)) {
    const lead = text.slice(0, 220);
    const m2 = lead.match(/\b(Build|Buy|Partner)\b/i);
    if (m2) {
      const key = m2[1].toUpperCase();
      return RECOMMENDATIONS[key] ?? null;
    }
  }
  return null;
}

// Split a section's prose into a lead sentence + the rest, so the lead can be
// styled like a pull-quote. Skip if the section starts with a list marker, a
// blockquote, a heading, or a code fence — those structures speak for themselves.
function splitLeadSentence(text: string): { lead: string | null; rest: string } {
  const trimmed = text.trimStart();
  if (!trimmed) return { lead: null, rest: text };
  // Don't split structured content.
  if (/^([-*+]\s|\d+\.\s|>|#|```)/.test(trimmed)) return { lead: null, rest: text };
  // First sentence: up to the first '.', '!', '?' followed by space/newline, or first newline.
  const m = trimmed.match(/^(.+?[.!?])(\s+|$)/s);
  if (!m) return { lead: null, rest: text };
  const lead = m[1].trim();
  // Skip if the "lead" is suspiciously long (likely no sentence boundary in a short paragraph).
  if (lead.length > 240) return { lead: null, rest: text };
  // Skip if the lead is suspiciously short (probably an abbreviation, e.g. "Q3.")
  if (lead.length < 18) return { lead: null, rest: text };
  const rest = trimmed.slice(m[0].length);
  return { lead, rest };
}

function DiffText({ oldText, newText }: { oldText: string; newText: string }) {
  const ops = useMemo(() => wordDiff(oldText, newText), [oldText, newText]);
  return (
    <div className="pb-diff-text">
      {ops.map((op, i) => {
        if (op.type === "equal") return <span key={i}>{op.text}</span>;
        if (op.type === "add") return <ins key={i} className="pb-diff-add">{op.text}</ins>;
        return <del key={i} className="pb-diff-remove">{op.text}</del>;
      })}
    </div>
  );
}

function CollapsibleSources({
  label, sources,
}: { label: string; sources: Array<{ title: string; url: string }> }) {
  const [open, setOpen] = useState(false);
  if (!sources?.length) return null;
  return (
    <details className="pb-sources-collapsible" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        {label} <span className="muted-note">({sources.length})</span>
      </summary>
      <div className="pb-step-web">
        {sources.map((s) => (
          <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
        ))}
      </div>
    </details>
  );
}

function modelShortLabel(id: string): string {
  if (id.includes("opus")) return "Opus";
  if (id.includes("sonnet")) return "Sonnet";
  if (id.includes("haiku")) return "Haiku";
  return id;
}

function horizonLabel(h: string): string {
  return ({
    "3mo": "3 months",
    "6mo": "6 months",
    "1y": "1 year",
    "3y": "3 years",
    "5y": "5 years",
  } as Record<string, string>)[h] ?? h;
}

function stepTypeLabel(type: string): string {
  switch (type) {
    case "intent_turn": return "ASK";
    case "foresight": return "DEBATE";
    case "simulate": return "SIM";
    case "factcheck": return "FACTCHECK";
    case "synthesize": return "SYNTH";
    default: return type;
  }
}

// ---------- Type-specific spotlight ----------------------------------------
// A small visual layer above the generic section list that gives the most
// information-dense artifact types a layout that fits how readers actually
// scan them: comparison cards for BuildBuy, ranked candidate cards for
// OpportunityScan, a corrections table for KBHealthReport. The full prose
// still renders below for context — the spotlight is a magazine cover, not
// a replacement.

function BriefSpotlight({
  artifactType, sections,
}: {
  artifactType: string;
  sections: Record<string, string>;
}) {
  if (artifactType === "BuildBuyDecision") {
    return <BuildBuySpotlight sections={sections} />;
  }
  if (artifactType === "OpportunityScan") {
    return <OpportunitySpotlight sections={sections} />;
  }
  if (artifactType === "KBHealthReport") {
    return <KBHealthSpotlight sections={sections} />;
  }
  return null;
}

function findSection(sections: Record<string, string>, predicate: (key: string) => boolean): string | null {
  for (const [k, v] of Object.entries(sections)) {
    if (predicate(k.toLowerCase())) return v || "";
  }
  return null;
}

function BuildBuySpotlight({ sections }: { sections: Record<string, string> }) {
  const cards = [
    { label: "Build", tone: "build", body: findSection(sections, (k) => /^build\b/.test(k)) },
    { label: "Buy",   tone: "buy",   body: findSection(sections, (k) => /^buy\b/.test(k)) },
    { label: "Partner", tone: "partner", body: findSection(sections, (k) => /^partner\b/.test(k)) },
  ].filter((c) => c.body && c.body.trim());
  if (cards.length < 2) return null;
  const recommendation = findSection(sections, (k) => /^recommend/.test(k));
  return (
    <div className="pb-spotlight">
      <div className="pb-spotlight-label">Side-by-side comparison</div>
      {recommendation && (
        <div className="pb-spotlight-verdict">
          <span className="pb-spotlight-verdict-tag">Recommendation</span>
          <InlineMarkdown>{firstLine(recommendation)}</InlineMarkdown>
        </div>
      )}
      <div className="pb-bbp-grid">
        {cards.map((c) => (
          <div key={c.label} className={`pb-bbp-card pb-bbp-card-${c.tone}`}>
            <h4>{c.label}</h4>
            <MarkdownView className="pb-bbp-body">{c.body!}</MarkdownView>
          </div>
        ))}
      </div>
    </div>
  );
}

function OpportunitySpotlight({ sections }: { sections: Record<string, string> }) {
  const ranked =
    findSection(sections, (k) => /top opportunit|ranked|top opportunities/.test(k)) ||
    findSection(sections, (k) => /top.{0,8}idea|opportunit/.test(k));
  if (!ranked) return null;
  const candidates = parseRankedList(ranked).slice(0, 5);
  if (candidates.length === 0) return null;
  return (
    <div className="pb-spotlight">
      <div className="pb-spotlight-label">Top candidates</div>
      <div className="pb-opp-grid">
        {candidates.map((c, i) => (
          <div key={i} className="pb-opp-card">
            <div className="pb-opp-rank">{i + 1}</div>
            <div className="pb-opp-body">
              <InlineMarkdown>{c}</InlineMarkdown>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function KBHealthSpotlight({ sections }: { sections: Record<string, string> }) {
  const outdated = findSection(sections, (k) => /outdated|stale|corrected/.test(k)) || "";
  const contradicted = findSection(sections, (k) => /contradicted/.test(k)) || "";
  const stillTrue = findSection(sections, (k) => /still.?true|confirmed/.test(k)) || "";
  const o = parseClaimList(outdated);
  const c = parseClaimList(contradicted);
  const t = parseClaimList(stillTrue);
  if (o.length + c.length + t.length === 0) return null;
  return (
    <div className="pb-spotlight">
      <div className="pb-spotlight-label">Claim audit</div>
      <table className="pb-claims-table">
        <thead><tr><th>Status</th><th>Claim</th></tr></thead>
        <tbody>
          {c.map((claim, i) => (
            <tr key={`c-${i}`}><td><span className="pb-claim-pill pb-claim-contradicted">✕ contradicted</span></td><td><InlineMarkdown>{claim}</InlineMarkdown></td></tr>
          ))}
          {o.map((claim, i) => (
            <tr key={`o-${i}`}><td><span className="pb-claim-pill pb-claim-outdated">⚠ outdated</span></td><td><InlineMarkdown>{claim}</InlineMarkdown></td></tr>
          ))}
          {t.map((claim, i) => (
            <tr key={`t-${i}`}><td><span className="pb-claim-pill pb-claim-confirmed">✓ still true</span></td><td><InlineMarkdown>{claim}</InlineMarkdown></td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function firstLine(s: string): string {
  for (const line of s.split("\n")) {
    const trimmed = line.trim().replace(/^[-*•]\s+/, "").replace(/^\d+\.\s+/, "");
    if (trimmed) return trimmed;
  }
  return s.trim();
}

// Pull list-like entries out of a section body. Accepts bullets (`- foo`),
// numbered (`1. foo`), and "##"-prefixed sub-headers. Each entry is the first
// non-empty line of that item; multi-line bullets collapse to one summary.
function parseRankedList(body: string): string[] {
  const lines = body.split("\n");
  const items: string[] = [];
  let current: string | null = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      if (current) { items.push(current); current = null; }
      continue;
    }
    const m = line.match(/^(?:\d+[.)]\s+|[-*•]\s+|#+\s+)(.+)$/);
    if (m) {
      if (current) items.push(current);
      current = m[1];
    } else if (current) {
      // Skip continuation lines — keep the headline only.
    }
  }
  if (current) items.push(current);
  return items.filter(Boolean);
}

function parseClaimList(body: string): string[] {
  return parseRankedList(body);
}

function highlightIcon(tone: string): string {
  switch (tone) {
    case "win":     return "🎯";
    case "risk":    return "🚨";
    case "number":  return "📈";
    case "tension": return "⚔️";
    default:        return "💡";
  }
}

// Section-name → icon. Keyword-matched against the visible label so it works
// across every playbook without per-playbook configuration.
export function sectionIcon(label: string): string | null {
  const s = label.toLowerCase();
  if (/^tl;?dr/.test(s)) return "✦";
  if (/recommend|verdict|decision|next step|next move/.test(s)) return "🎯";
  if (/risk|failure|killing blow|blocker|blind spot|adversari|red.?team/.test(s)) return "🚨";
  if (/opportunit|white space|unexplored|bet|idea/.test(s)) return "💡";
  if (/arr|revenue|cost|price|pricing|metric|north star/.test(s)) return "📈";
  if (/debate|convergen|divergen|bull|bear|investor|customer|competitor|persona/.test(s)) return "⚔️";
  if (/architecture|spec|scope|story|prd|design/.test(s)) return "📐";
  if (/feasib|effort|technical/.test(s)) return "🛠";
  if (/launch|gtm|icp|positioning|battlecard|channel/.test(s)) return "🚀";
  if (/assumpt|load.?bearing|watch|leading indicator/.test(s)) return "🔭";
  if (/historic|analogue|precedent/.test(s)) return "📚";
  if (/source/.test(s)) return "🔗";
  return null;
}


// ---- Follow-up Q&A panel -------------------------------------------------

type ArtifactQAPanelProps = {
  history: ArtifactQA[];
  question: string;
  pending: boolean;
  error: string | null;
  onChange: (q: string) => void;
  onSubmit: () => void;
  onDelete: (qaId: string) => void;
};

function ArtifactQAPanel({
  history, question, pending, error, onChange, onSubmit, onDelete,
}: ArtifactQAPanelProps) {
  return (
    <section className="pb-qa-panel" aria-label="Ask follow-up questions">
      <header className="pb-qa-head">
        <h3>💬 Ask follow-up questions</h3>
        <span className="muted-note">
          Answers are grounded in this brief + the full step transcript.
        </span>
      </header>

      {history.length > 0 && (
        <ol className="pb-qa-list">
          {history.map((q) => (
            <li key={q.id} className="pb-qa-item">
              <div className="pb-qa-q">
                <strong>Q.</strong> {q.question}
                <button
                  className="link-btn pb-qa-del"
                  onClick={() => onDelete(q.id)}
                  title="Remove this Q&A"
                >
                  ✕
                </button>
              </div>
              <div className="pb-qa-a">
                <MarkdownView>{q.answer}</MarkdownView>
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="pb-qa-composer">
        <textarea
          rows={2}
          placeholder="e.g. Which idea has the cheapest validation, and why?"
          value={question}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              onSubmit();
            }
          }}
          disabled={pending}
        />
        <div className="pb-qa-actions">
          <span className="muted-note">⌘/Ctrl + Enter to ask</span>
          <button
            className="btn-primary small"
            onClick={onSubmit}
            disabled={pending || !question.trim()}
          >
            {pending ? <><span className="spinner" /> Thinking…</> : "Ask"}
          </button>
        </div>
      </div>

      {error && <div className="pb-run-error">Ask failed: {error}</div>}
    </section>
  );
}
