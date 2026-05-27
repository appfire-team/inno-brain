// Typed API client for the InnoBrain backend.

export type Stats = {
  nodes: number;
  edges: number;
  communities: number;
  files: number;
  // Per-source-type breakdown. `docs` covers drag-and-drop / PDFs, `urls`
  // covers /api/ingest-url fetches, `research` covers /api/research outputs,
  // `repos` mirrors /api/docs.repos length. Always present in fresh responses.
  sources?: { docs: number; urls: number; research: number; repos: number };
  has_graph: boolean;
  embeddings?: { available: boolean; built?: boolean; size?: number; model?: string; dim?: number };
  indexing?: IndexJob;
};

export type IndexJobStatus = "idle" | "running" | "complete" | "failed";
export type IndexJob = {
  id?: string;
  workspace_id?: string;
  kind?: string;        // "ingest_repo" | "upload" | "rebuild" | "delete_repo" | "delete_doc" | "ingest_url" | "web_research"
  label?: string;       // human-readable summary (e.g. "Indexing resolver-core (1500 files)")
  status: IndexJobStatus;
  message?: string;
  started_at?: number;
  finished_at?: number;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

export type UploadedFile = {
  name: string;
  size: number;
  modified: number;
};

export type IngestedRepo = {
  name: string;
  file_count: number;
  bytes: number;
  modified: number;
};

export type SubgraphNode = {
  id: string;
  label: string;
  source_file: string | null;
  file_type: string | null;
  community: number | null;
  relevance: number;
  is_start: boolean;
};

export type SubgraphEdge = {
  source: string;
  target: string;
  relation: string | null;
  confidence: string | null;
  confidence_score: number | null;
};

export type QueryResult = {
  start_nodes?: string[];
  mode?: string;
  terms?: string[];
  fallback_used?: boolean;
  subgraph: { nodes: SubgraphNode[]; edges: SubgraphEdge[] };
  rendered?: string;
  answer?: string;
  answer_error?: string;
  error?: string;
  web_sources?: Array<{ title: string; url: string }>;
  // Structured gap analysis — what the corpus is silent on / stale on /
  // weakly supports for this question. Always present (possibly empty).
  gaps?: string[];
};

export type UploadResult = {
  saved: string[];
  result: {
    nodes: number;
    edges: number;
    communities: number;
    files: number;
    message?: string;
    meta?: {
      backend: string | null;
      extracted_files: number;
      error?: string;
      input_tokens?: number;
      output_tokens?: number;
      errors?: string[];
    };
  };
};

export type GodNode = { id: string; label: string; degree: number };
export type Surprise = {
  source: string;
  target: string;
  source_label?: string;
  target_label?: string;
  relation?: string;
  confidence?: string;
};
export type SuggestedQuestion = { question: string; reason?: string };

export type Insights = {
  gods?: GodNode[];
  surprises?: Surprise[];
  questions?: SuggestedQuestion[];
  community_labels?: Record<string, string>;
  cohesion?: Record<string, number>;
};

export type Community = {
  id: number;
  label: string;
  size: number;
  cohesion: number | null;
  nodes: Array<{ id: string; label: string; source_file: string | null }>;
};

export type ExplainResult = {
  node?: {
    id: string;
    label: string;
    source_file?: string;
    community_label?: string;
    file_type?: string;
    degree?: number;
  };
  neighbors?: Array<{
    id: string;
    label: string;
    source_file: string | null;
    community_label: string | null;
    relation: string | null;
    confidence: string | null;
    confidence_score: number | null;
  }>;
  explanation?: string;
  error?: string;
};

export type PathHop = {
  id: string;
  label: string;
  source_file: string | null;
  community_label: string | null;
  out_relation?: string;
  out_confidence?: string;
  out_confidence_score?: number;
};
export type PathResult = {
  matched_source?: string;
  matched_target?: string;
  hop_count?: number;
  path?: PathHop[];
  error?: string;
};

export type RawGraphNode = {
  id: string;
  label?: string;
  source_file?: string;
  community?: number;
  community_label?: string;
  file_type?: string;
};
export type RawGraphLink = {
  source: string;
  target: string;
  relation?: string;
  confidence?: string;
  confidence_score?: number;
};
export type RawGraph = { nodes: RawGraphNode[]; links: RawGraphLink[] };

// ---- Workspace context ----
// The backend reads X-Workspace-Id on every workspace-scoped route. The active
// id is kept in localStorage so it survives page reloads.
const WS_STORAGE_KEY = "innobrain.activeWorkspace";
let _activeWorkspaceId: string | null =
  typeof window !== "undefined" ? window.localStorage.getItem(WS_STORAGE_KEY) : null;

export function getActiveWorkspaceId(): string | null {
  return _activeWorkspaceId;
}

export function setActiveWorkspaceId(id: string | null): void {
  _activeWorkspaceId = id;
  if (typeof window !== "undefined") {
    if (id) window.localStorage.setItem(WS_STORAGE_KEY, id);
    else window.localStorage.removeItem(WS_STORAGE_KEY);
  }
}

function workspaceHeaders(): Record<string, string> {
  return _activeWorkspaceId ? { "X-Workspace-Id": _activeWorkspaceId } : {};
}

/** Long requests routed through an ngrok free-tier tunnel hit a ~60s response
 * timeout and we get an HTML error page back instead of JSON. Convert that to
 * a short, friendly message so the user knows the backend is still working. */
function friendlyTunnelMessage(rawBody: string, contentType: string | null): string | null {
  const looksHtml = (contentType || "").includes("text/html") || rawBody.trimStart().startsWith("<");
  if (!looksHtml) return null;
  if (/ERR_NGROK|ngrok gateway/i.test(rawBody)) {
    return "Still working… the tunnel timed out at 60s but the backend keeps running. Refresh in a minute or two to see results.";
  }
  // Generic HTML-instead-of-JSON (proxy / CDN / 502): keep the message tight.
  return "Server didn't return a normal response. It may still be working in the background — refresh shortly.";
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...workspaceHeaders(),
        ...(init?.headers ?? {}),
      },
    });
  } catch (e) {
    // Network-level failure (DNS, CORS, offline, dropped connection mid-request).
    throw new Error("Couldn't reach the backend — check your connection or wait a moment and retry.");
  }
  if (!res.ok) {
    const raw = await res.text().catch(() => "");
    const friendly = friendlyTunnelMessage(raw, res.headers.get("content-type"));
    if (friendly) throw new Error(friendly);
    // FastAPI returns {"detail": "..."} on HTTPException. Unwrap it so the
    // toast shows a clean human message instead of raw JSON.
    let detail = raw;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.detail === "string") detail = parsed.detail;
    } catch { /* not JSON */ }
    throw new Error(detail ? detail : `${res.status} ${res.statusText}`);
  }
  // 200-but-HTML can happen too when the tunnel returns its own page above us.
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    const raw = await res.text().catch(() => "");
    const friendly = friendlyTunnelMessage(raw, ct);
    if (friendly) throw new Error(friendly);
  }
  return res.json() as Promise<T>;
}

export const api = {
  stats: () => jsonRequest<Stats>("/api/stats"),
  docs: () => jsonRequest<{ files: UploadedFile[]; repos: IngestedRepo[] }>("/api/docs"),
  indexJob: () => jsonRequest<IndexJob>("/api/index-job"),
  ingestRepo: (path: string, name?: string) =>
    jsonRequest<{
      repo: string;
      source_path: string;
      copied: number;
      skipped_dirs: number;
      skipped_files: number;
      skipped_ignored?: number;
      ignore_active?: boolean;
      kbignore_present?: boolean;
      too_large_sample?: string[];
      bytes: number;
      job: IndexJob;
    }>("/api/ingest-repo", {
      method: "POST",
      body: JSON.stringify({ path, name: name ?? null }),
    }),
  deleteRepo: (name: string) =>
    jsonRequest<{ deleted: string; job: IndexJob }>(
      `/api/repos/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  upload: async (files: File[]): Promise<{ saved: string[]; job: IndexJob }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const res = await fetch("/api/upload", {
      method: "POST",
      body: fd,
      headers: workspaceHeaders(),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText} — ${detail}`);
    }
    return res.json();
  },
  deleteDoc: (filename: string) =>
    jsonRequest<{ deleted: string; job: IndexJob }>(
      `/api/docs/${encodeURIComponent(filename)}`,
      { method: "DELETE" }
    ),
  query: (question: string, mode: "bfs" | "dfs", synthesize: boolean, web_grounding = false) =>
    jsonRequest<QueryResult>("/api/query", {
      method: "POST",
      body: JSON.stringify({ question, mode, synthesize, web_grounding }),
    }),
  insights: () => jsonRequest<Insights>("/api/insights"),
  insightsWebContext: () =>
    jsonRequest<{
      god_context: Array<{ id: string; label: string; summary: string; web_sources: Array<{ title: string; url: string }> }>;
      suggested_questions: SuggestedQuestion[];
    }>("/api/insights/web-context", { method: "POST" }),
  communities: () => jsonRequest<{ communities: Community[] }>("/api/communities"),
  explain: (node: string, web_grounding = false) =>
    jsonRequest<ExplainResult>("/api/explain", {
      method: "POST",
      body: JSON.stringify({ node, web_grounding }),
    }),
  path: (source: string, target: string) =>
    jsonRequest<PathResult>("/api/path", {
      method: "POST",
      body: JSON.stringify({ source, target }),
    }),
  ingestUrl: (url: string, author?: string, contributor?: string) =>
    jsonRequest<{ saved: string; web_sources?: Array<{ title: string; url: string }>; job: IndexJob }>(
      "/api/ingest-url",
      {
        method: "POST",
        body: JSON.stringify({ url, author, contributor }),
      },
    ),
  relink: () =>
    jsonRequest<RelinkResult>("/api/relink", { method: "POST" }),
  rebuild: () =>
    jsonRequest<{ job: IndexJob }>("/api/rebuild", { method: "POST" }),
  graph: () => jsonRequest<RawGraph>("/api/graph"),

  // --- conversations ---
  conversations: () => jsonRequest<{ conversations: ConversationSummary[] }>("/api/conversations"),
  createConversation: (opts: {
    title: string;
    intent?: string | null;
    rubric_id?: string | null;
    inference_strategy?: string;
    web_grounding?: boolean;
    auto_memory?: boolean;
    answer_model?: string | null;
  }) =>
    jsonRequest<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(opts),
    }),
  updateConversationSettings: (
    id: string,
    settings: {
      intent?: string | null;
      rubric_id?: string | null;
      inference_strategy?: string;
      web_grounding?: boolean;
      auto_memory?: boolean;
      answer_model?: string | null;
    }
  ) =>
    jsonRequest<Conversation>(`/api/conversations/${id}/settings`, {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  synthesizeScenariosFromConversation: (id: string) =>
    jsonRequest<{ scenarios: string[] }>(`/api/conversations/${id}/synthesize-scenarios`, {
      method: "POST",
    }),
  models: () => jsonRequest<{ models: ModelOption[]; default: string }>("/api/models"),

  // --- foresight ---
  foresightPersonas: () => jsonRequest<{ personas: ForesightPersona[] }>("/api/foresight/personas"),
  foresightCreatePersona: (body: { label: string; tagline?: string; system: string; color?: string | null }) =>
    jsonRequest<ForesightPersona>("/api/foresight/personas", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  foresightUpdatePersona: (id: string, body: { label?: string; tagline?: string; system?: string; color?: string | null }) =>
    jsonRequest<ForesightPersona>(`/api/foresight/personas/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  foresightDeletePersona: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/foresight/personas/${encodeURIComponent(id)}`, { method: "DELETE" }),
  foresightRestorePreset: (id: string) =>
    jsonRequest<ForesightPersona>(`/api/foresight/personas/${encodeURIComponent(id)}/restore-default`, {
      method: "POST",
    }),
  foresightHorizons: () => jsonRequest<{ horizons: Record<string, string> }>("/api/foresight/horizons"),
  foresightSessions: () => jsonRequest<{ sessions: ForesightSessionSummary[] }>("/api/foresight/sessions"),
  foresightCreateSession: (body: {
    title: string;
    scenario: string;
    horizon: string;
    persona_ids: string[];
    rounds: number;
    world_context?: string;
    rubric_id?: string | null;
    use_graph?: boolean;
    answer_model?: string | null;
    source_conversation_id?: string | null;
    source_conversation_title?: string | null;
    use_memory?: boolean;
    web_grounding?: boolean;
    synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
  }) =>
    jsonRequest<ForesightSession>("/api/foresight/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  foresightGetSession: (id: string) =>
    jsonRequest<ForesightSession>(`/api/foresight/sessions/${id}`),
  foresightUpdateSession: (id: string, body: Partial<ForesightSession>) =>
    jsonRequest<ForesightSession>(`/api/foresight/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  foresightDeleteSession: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/foresight/sessions/${id}`, { method: "DELETE" }),
  foresightRun: (id: string) =>
    jsonRequest<ForesightSession>(`/api/foresight/sessions/${id}/run`, { method: "POST" }),
  exportConversation: (id: string) =>
    jsonRequest<{ markdown: string }>(`/api/conversations/${id}/export`, {
      method: "POST",
    }),
  simulate: (
    id: string,
    question: string,
    horizon: string,
    opts: { useGraph?: boolean; webGrounding?: boolean; useMemory?: boolean } = {},
  ) =>
    jsonRequest<Conversation>(`/api/conversations/${id}/simulate`, {
      method: "POST",
      body: JSON.stringify({
        question,
        horizon,
        use_graph: opts.useGraph ?? true,
        web_grounding: opts.webGrounding ?? false,
        use_memory: opts.useMemory ?? true,
      }),
    }),
  simulatePersonas: () =>
    jsonRequest<{
      personas: Array<{ key: string; label: string; tagline: string }>;
      horizons: Record<string, string>;
    }>("/api/simulate/personas"),

  // --- intents + rubrics ---
  // Note: the backend now returns groups as arrays of {id, label, source} so the
  // UI can render builtin vs user badges. Old shape (Record<string,string>) is
  // also still served by some older clients — kept loose here.
  intents: () =>
    jsonRequest<{
      intents: Record<string, string>;
      groups?: Array<{
        label: string;
        intents: Array<{ id: string; label: string; source: IntentSource }> | Record<string, string>;
      }>;
    }>("/api/intents"),
  customIntents: () =>
    jsonRequest<{ intents: CustomIntent[] }>("/api/intents/custom"),
  getIntentSource: (id: string) =>
    jsonRequest<{ id: string; label: string; group: string; body: string; source: IntentSource }>(
      `/api/intents/source/${id}`,
    ),
  createIntent: (body: {
    id: string; group: string; label: string; body: string; scope: IntentScope;
  }) =>
    jsonRequest<CustomIntent>("/api/intents/custom", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateIntent: (id: string, body: { group?: string; label?: string; body?: string }) =>
    jsonRequest<CustomIntent>(`/api/intents/custom/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteIntent: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/intents/custom/${id}`, { method: "DELETE" }),
  cloneIntent: (id: string, body: { new_id?: string; scope?: IntentScope } = {}) =>
    jsonRequest<CustomIntent>(`/api/intents/${id}/clone`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  restoreDefaultIntent: (id: string) =>
    jsonRequest<{ id: string; label: string; group: string; body: string; source: IntentSource }>(
      `/api/intents/${id}/restore-default`,
      { method: "POST" },
    ),
  rubrics: () => jsonRequest<{ rubrics: Rubric[] }>("/api/rubrics"),
  availableRubrics: () =>
    jsonRequest<{ rubrics: AvailableRubric[] }>("/api/rubrics/available"),
  createRubric: (name: string, body: string) =>
    jsonRequest<Rubric>("/api/rubrics", {
      method: "POST",
      body: JSON.stringify({ name, body }),
    }),
  updateRubric: (id: string, name?: string, body?: string) =>
    jsonRequest<Rubric>(`/api/rubrics/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name, body }),
    }),
  deleteRubric: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/rubrics/${id}`, { method: "DELETE" }),
  restoreDefaultRubric: (id: string) =>
    jsonRequest<Rubric>(`/api/rubrics/${id}/restore-default`, { method: "POST" }),

  // --- KB refinements / corrections ---
  kbCorrections: () =>
    jsonRequest<{ corrections: KBCorrection[] }>("/api/kb/corrections"),
  createKBCorrection: (body: Partial<KBCorrection>) =>
    jsonRequest<KBCorrection>("/api/kb/corrections", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateKBCorrection: (id: string, body: Partial<KBCorrection>) =>
    jsonRequest<KBCorrection>(`/api/kb/corrections/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteKBCorrection: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/kb/corrections/${id}`, { method: "DELETE" }),
  searchKBNodes: (
    q: string,
    opts: {
      limit?: number;
      offset?: number;
      community?: string | null;
      source_file?: string | null;
      entity_type?: string | null;
      include_code?: boolean;
    } = {},
  ) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (opts.limit != null) params.set("limit", String(opts.limit));
    if (opts.offset != null) params.set("offset", String(opts.offset));
    if (opts.community) params.set("community", opts.community);
    if (opts.source_file) params.set("source_file", opts.source_file);
    if (opts.entity_type) params.set("entity_type", opts.entity_type);
    if (opts.include_code) params.set("include_code", "true");
    return jsonRequest<{
      results: KBNodeMatch[];
      total: number;
      communities: Array<{ label: string; count: number }>;
      source_files: Array<{ label: string; count: number }>;
      entity_types: Array<{ label: string; count: number }>;
    }>(`/api/kb/nodes/search?${params.toString()}`);
  },
  // Typed-entity layer (deterministic, LLM-free post-pass).
  listEntities: (opts: { entity_type?: string | null; q?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.entity_type) params.set("entity_type", opts.entity_type);
    if (opts.q) params.set("q", opts.q);
    if (opts.limit != null) params.set("limit", String(opts.limit));
    return jsonRequest<{
      results: Array<{
        id: string;
        label: string;
        entity_type: string;
        source_file: string | null;
        community_label: string | null;
      }>;
      total: number;
      type_counts: Record<string, number>;
    }>(`/api/entities?${params.toString()}`);
  },
  // Role-typed edges — "who works at Acme?" / "what did Bob invest in?"
  listEntityRelations: (opts: {
    relation?: string | null;
    subject_label?: string | null;
    target_label?: string | null;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (opts.relation) params.set("relation", opts.relation);
    if (opts.subject_label) params.set("subject_label", opts.subject_label);
    if (opts.target_label) params.set("target_label", opts.target_label);
    if (opts.limit != null) params.set("limit", String(opts.limit));
    return jsonRequest<{
      results: Array<{
        relation: string;
        subject: { id: string; label: string; entity_type: string | null };
        target: { id: string; label: string; entity_type: string | null };
        source_file: string | null;
        confidence: string | null;
        extractor: string | null;
      }>;
      total: number;
      relations: string[];
    }>(`/api/entities/relations?${params.toString()}`);
  },
  kbDiff: () => jsonRequest<{ diff: KBDiff | null }>("/api/kb/diff"),

  // --- memory ---
  memory: () => jsonRequest<{ items: MemoryItem[] }>("/api/memory"),
  createMemory: (text: string, tag?: string | null) =>
    jsonRequest<MemoryItem>("/api/memory", {
      method: "POST",
      body: JSON.stringify({ text, tag: tag ?? null }),
    }),
  updateMemory: (id: string, text?: string, tag?: string | null) =>
    jsonRequest<MemoryItem>(`/api/memory/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ text, tag }),
    }),
  deleteMemory: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/memory/${id}`, { method: "DELETE" }),
  getConversation: (id: string) => jsonRequest<Conversation>(`/api/conversations/${id}`),
  deleteConversation: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/conversations/${id}`, { method: "DELETE" }),
  renameConversation: (id: string, title: string) =>
    jsonRequest<Conversation>(`/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  addTurn: (id: string, text: string) =>
    jsonRequest<Conversation>(`/api/conversations/${id}/turn`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  addPin: (id: string, pin: Omit<Pin, "id" | "ts">) =>
    jsonRequest<Conversation>(`/api/conversations/${id}/pin`, {
      method: "POST",
      body: JSON.stringify(pin),
    }),
  removePin: (convId: string, pinId: string) =>
    jsonRequest<Conversation>(`/api/conversations/${convId}/pin/${pinId}`, {
      method: "DELETE",
    }),

  // --- workspaces ---
  workspaces: () => jsonRequest<{ workspaces: WorkspaceSummary[] }>("/api/workspaces"),
  createWorkspace: (
    name: string,
    source_workspace_id?: string | null,
    seed_rubrics?: Array<{ workspace_id: string | null; rubric_id: string }> | null,
  ) =>
    jsonRequest<WorkspaceSummary>("/api/workspaces", {
      method: "POST",
      body: JSON.stringify({
        name,
        source_workspace_id: source_workspace_id ?? null,
        seed_rubrics: seed_rubrics ?? null,
      }),
    }),
  renameWorkspace: (id: string, name: string) =>
    jsonRequest<WorkspaceSummary>(`/api/workspaces/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteWorkspace: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/workspaces/${id}`, { method: "DELETE" }),

  // --- web research ---
  webResearch: (query: string, filename?: string) =>
    jsonRequest<{
      saved: string;
      web_sources: Array<{ title: string; url: string }>;
      job: IndexJob;
    }>("/api/research", {
      method: "POST",
      body: JSON.stringify({ query, filename: filename ?? null }),
    }),

  // --- playbooks ---
  playbooks: () =>
    jsonRequest<{
      playbooks: PlaybookTemplate[];
      artifact_types: Record<string, string>;
      horizons: Record<string, string>;
      synth_inference_strategies: Array<"none" | "reflection" | "cove" | "best_of_3">;
    }>("/api/playbooks"),
  runPlaybook: (body: {
    playbook_id: string;
    scenario: string;
    horizon?: string;
    source_artifact_id?: string | null;
    rubric_id?: string | null;
    web_grounding?: boolean;
    synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
    fact_check?: boolean;
    answer_model?: string | null;
  }) =>
    jsonRequest<PlaybookRun>("/api/playbooks/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  playbookRuns: () =>
    jsonRequest<{ runs: PlaybookRunSummary[] }>("/api/playbooks/runs"),
  getPlaybookRun: (id: string) =>
    jsonRequest<PlaybookRun>(`/api/playbooks/runs/${id}`),
  deletePlaybookRun: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/playbooks/runs/${id}`, { method: "DELETE" }),
  cancelPlaybookRun: (id: string) =>
    jsonRequest<PlaybookRun>(`/api/playbooks/runs/${id}/cancel`, { method: "POST" }),
  resumePlaybookRun: (id: string) =>
    jsonRequest<PlaybookRun>(`/api/playbooks/runs/${id}/resume`, { method: "POST" }),
  getPlaybookSpec: (id: string) =>
    jsonRequest<PlaybookSpec>(`/api/playbooks/source/${id}`),
  createCustomPlaybook: (spec: PlaybookSpec) =>
    jsonRequest<PlaybookSpec>("/api/playbooks/custom", {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  updateCustomPlaybook: (id: string, spec: PlaybookSpec) =>
    jsonRequest<PlaybookSpec>(`/api/playbooks/custom/${id}`, {
      method: "PATCH",
      body: JSON.stringify(spec),
    }),
  deleteCustomPlaybook: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/playbooks/custom/${id}`, { method: "DELETE" }),
  clonePlaybook: (id: string, body: { new_id?: string; scope?: IntentScope } = {}) =>
    jsonRequest<PlaybookSpec>(`/api/playbooks/${id}/clone`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  restoreDefaultPlaybook: (id: string) =>
    jsonRequest<PlaybookSpec>(`/api/playbooks/${id}/restore-default`, { method: "POST" }),
  suggestPlaybookScenarios: (id: string, fresh: boolean = false) =>
    jsonRequest<{ scenarios: Array<{ text: string; kind: "kb" | "wildcard" }> }>(
      `/api/playbooks/${id}/suggest-scenarios${fresh ? "?fresh=true" : ""}`,
    ),

  // --- artifacts ---
  artifacts: (artifactType?: string) =>
    jsonRequest<{ artifacts: ArtifactSummary[]; types: Record<string, string> }>(
      artifactType
        ? `/api/artifacts?artifact_type=${encodeURIComponent(artifactType)}`
        : "/api/artifacts",
    ),
  getArtifact: (id: string) => jsonRequest<Artifact>(`/api/artifacts/${id}`),
  deleteArtifact: (id: string) =>
    jsonRequest<{ deleted: string }>(`/api/artifacts/${id}`, { method: "DELETE" }),
  renameArtifact: (id: string, title: string) =>
    jsonRequest<Artifact>(`/api/artifacts/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  askArtifact: (id: string, question: string) =>
    jsonRequest<ArtifactQA>(`/api/artifacts/${id}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getArtifactQA: (id: string) =>
    jsonRequest<{ qa_history: ArtifactQA[] }>(`/api/artifacts/${id}/qa`),
  deleteArtifactQA: (artifactId: string, qaId: string) =>
    jsonRequest<{ deleted: string }>(
      `/api/artifacts/${artifactId}/qa/${qaId}`,
      { method: "DELETE" },
    ),
  simplifyArtifact: (id: string, force = false) =>
    jsonRequest<ArtifactSimplified>(`/api/artifacts/${id}/simplify`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  addArtifactComment: (id: string, body: { text: string; author?: string; section?: string | null }) =>
    jsonRequest<ArtifactComment>(`/api/artifacts/${id}/comments`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateArtifactComment: (
    artifactId: string,
    commentId: string,
    body: { status?: ArtifactComment["status"]; text?: string },
  ) =>
    jsonRequest<ArtifactComment>(`/api/artifacts/${artifactId}/comments/${commentId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  refineArtifact: (
    id: string,
    opts?: { instruction?: string; include_qa?: boolean; include_conversation?: boolean },
  ) =>
    jsonRequest<Artifact>(`/api/artifacts/${id}/refine`, {
      method: "POST",
      body: JSON.stringify(opts ?? {}),
    }),
  suggestPatch: (childId: string, body: { parent_id: string; from_version?: number; to_version?: number }) =>
    jsonRequest<PatchSuggestion>(`/api/artifacts/${childId}/suggest-patch`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createArtifact: (body: {
    artifact_type: string;
    title?: string;
    tldr?: string;
    sections?: Record<string, string> | null;
    raw_markdown: string;
    highlights?: ArtifactHighlight[] | null;
    provenance?: Record<string, unknown> | null;
  }) =>
    jsonRequest<Artifact>(`/api/artifacts`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  resyncArtifactFromConversation: (artId: string) =>
    jsonRequest<Artifact>(`/api/artifacts/${artId}/resync-from-conversation`, {
      method: "POST",
    }),

  // --- influence explainer ("Why this answer?") ---
  explainTurnInfluence: (convId: string, turnIdx: number) =>
    jsonRequest<TurnInfluence>(
      `/api/conversations/${convId}/turn/${turnIdx}/explain-influence`,
    ),
  explainRunInfluence: (runId: string) =>
    jsonRequest<RunInfluence>(`/api/playbooks/runs/${runId}/explain-influence`),
};

export type Influence = {
  kind: string;
  id: string;
  label: string;
  weight: "high" | "medium" | "low" | string;
  evidence: string;
};

export type InfluenceLever = {
  id: string;
  label: string;
  expected_effect: string;
  /** Each lever describes a settings PATCH the UI applies to the conversation
   * before re-issuing the same question. Other shapes are reserved. */
  change: { settings: Record<string, unknown> };
};

export type TurnInfluence = {
  turn_idx: number;
  question: string;
  answer_preview: string;
  settings: {
    intent?: string | null;
    rubric_id?: string | null;
    inference_strategy?: string;
    web_grounding?: boolean;
    answer_model?: string | null;
    auto_memory?: boolean;
  };
  summary: string;
  influences: Influence[];
  levers: InfluenceLever[];
  raw_signals?: Record<string, unknown>;
  convergence_theme?: null;
};

export type RunInfluence = {
  run_id: string;
  playbook_id: string;
  summary: string;
  convergence_theme?: string | null;
  influences: Influence[];
  /** Always empty for runs in v1; reserved for future per-run lever support. */
  levers: InfluenceLever[];
  raw_signals?: Record<string, unknown>;
  note?: string;
};

export type PlaybookStepTemplate = {
  id: string;
  label: string;
  type: "intent_turn" | "foresight" | "simulate" | "factcheck" | "synthesize";
  intent?: string;
};

export type PlaybookTemplate = {
  id: string;
  label: string;
  tagline: string;
  expected_duration_s: number;
  accepts_source_types: string[];
  artifact_type: string;
  steps: PlaybookStepTemplate[];
  source?: "builtin" | "customized" | "workspace" | "global";
};

export type IntentSource = "builtin" | "customized" | "workspace" | "global";
export type IntentScope = "workspace" | "global";

export type CustomIntent = {
  id: string;
  group: string;
  label: string;
  body: string;
  scope: IntentScope;
  created_at: number;
  updated_at: number;
};

export type PlaybookStepSpec = {
  id: string;
  label: string;
  type: "intent_turn" | "foresight" | "simulate" | "factcheck" | "synthesize";
  // intent_turn
  intent?: string;
  // foresight + simulate
  personas?: string[];
  rounds?: number;
  horizon?: string;
  // synthesize
  sections?: string[];
};

export type PlaybookSpec = {
  id: string;
  label: string;
  tagline: string;
  expected_duration_s: number;
  accepts_source_types: string[];
  artifact_type: string;
  steps: PlaybookStepSpec[];
  scope?: IntentScope;
  source?: "builtin" | "customized" | "workspace" | "global";
};

export type PlaybookStepStatus = "pending" | "running" | "complete" | "failed";

export type PlaybookStep = {
  id: string;
  label: string;
  type: "intent_turn" | "foresight" | "simulate" | "factcheck" | "synthesize";
  status: PlaybookStepStatus;
  started_at: number | null;
  finished_at: number | null;
  tokens: { input: number; output: number };
  output: string;
  web_sources: Array<{ title: string; url: string }>;
};

export type PlaybookRun = {
  id: string;
  playbook_id: string;
  playbook_label: string;
  workspace_id: string;
  user_inputs: {
    scenario: string;
    horizon: string;
    source_artifact_id: string | null;
    rubric_id: string | null;
    web_grounding: boolean;
    synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
    fact_check?: boolean;
    answer_model?: string | null;
  };
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  current_step: number;
  cancel_requested?: boolean;
  steps: PlaybookStep[];
  total_tokens: { input: number; output: number };
  started_at: number;
  finished_at: number | null;
  final_artifact_id: string | null;
  error: string | null;
  web_sources?: Array<{ title: string; url: string }>;
};

export type PlaybookRunSummary = {
  id: string;
  playbook_id: string;
  playbook_label: string;
  status: PlaybookRun["status"];
  current_step: number;
  step_count: number;
  started_at: number;
  finished_at: number | null;
  final_artifact_id: string | null;
  scenario: string;
};

export type ArtifactSummary = {
  id: string;
  type: string;
  title: string;
  tldr: string;
  created_at: number;
  updated_at: number;
  playbook_id?: string;
  playbook_run_id?: string;
  source_artifact_ids: string[];
};

export type ArtifactComment = {
  id: string;
  author: string;
  section: string | null;
  text: string;
  status: "open" | "addressed" | "resolved";
  created_at: number;
  addressed_in_version: number | null;
};

export type ArtifactHighlightTone = "win" | "risk" | "claim" | "tension" | "number";
export type ArtifactHighlight = { text: string; tone: ArtifactHighlightTone };

export type ArtifactVersion = {
  v: number;
  tldr: string;
  sections: Record<string, string>;
  highlights?: ArtifactHighlight[];
  raw_markdown: string;
  created_at: number;
  summary: string;
};

export type Artifact = {
  id: string;
  type: string;
  title: string;
  tldr: string;
  sections: Record<string, string>;
  highlights?: ArtifactHighlight[];
  raw_markdown: string;
  provenance: {
    playbook_id?: string;
    playbook_run_id?: string;
    scenario?: string;
    source_artifact_ids?: string[];
    web_sources?: Array<{ title: string; url: string }>;
    step_outputs?: ArtifactStepOutput[];
  };
  created_at: number;
  updated_at: number;
  current_version: number;
  versions: ArtifactVersion[];
  comments: ArtifactComment[];
  qa_history?: ArtifactQA[];
  simplified?: ArtifactSimplified;
};

export type ArtifactStepOutput = {
  id: string;
  label: string;
  type: string;
  output: string;
  tokens?: { input: number; output: number };
  web_sources?: Array<{ title: string; url: string }>;
};

export type ArtifactQA = {
  id: string;
  question: string;
  answer: string;
  tokens?: { input: number; output: number };
  created_at: number;
};

export type ArtifactSimplified = {
  body: string;
  tokens?: { input: number; output: number };
  created_at: number;
  source_updated_at?: number;
};

export type PatchSuggestion = {
  summary: string;
  parent_id: string;
  from_version: number;
  to_version: number;
  parent_changes: Array<{ section: string; before: string; after: string }>;
  suggested_changes: Array<{ section: string; rationale: string; proposed_text: string }>;
  instruction: string;
};

export type WorkspaceSummary = {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  source_workspace_id: string | null;
  stats: {
    documents: number;
    repos?: number;
    conversations: number;
    foresight_sessions: number;
    has_graph: boolean;
  };
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  turn_count: number;
  pin_count: number;
};

export type Pin = {
  id: string;
  ts: number;
  kind: "node" | "answer" | "note";
  label?: string | null;
  node_id?: string | null;
  text?: string | null;
};

export type SimulationPersona = {
  key: string;
  label: string;
  tagline?: string;
  text: string;
  tokens?: { input?: number; output?: number };
};

export type Simulation = {
  kind: "simulation";
  question: string;
  horizon: string;
  horizon_label: string;
  personas: (SimulationPersona & { web_sources?: Array<{ title: string; url: string }> })[];
  synthesis: string;
  tokens: { input: number; output: number };
  elapsed_ms: number;
  entry_node_labels?: string[];
  used_memory?: boolean;
  used_web_grounding?: boolean;
  used_intent?: boolean;
  synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
  synth_inference_steps?: Array<{ label: string; tokens: { input?: number; output?: number } }>;
  web_sources?: Array<{ title: string; url: string }>;
};

export type Turn =
  | { role: "user"; text: string; ts: number }
  | { role: "simulation"; text: string; ts: number; simulation: Simulation }
  | {
      role: "assistant";
      text: string;
      ts: number;
      grounded?: boolean;
      entry_node_ids?: string[];
      entry_node_labels?: string[];
      router_reasoning?: string;
      needs_graph?: boolean;
      subgraph_node_count?: number;
      subgraph?: {
        nodes: Array<{
          id: string;
          label: string;
          source_file: string | null;
          community_label: string | null;
          is_entry: boolean;
        }>;
        edges: Array<{
          source: string;
          target: string;
          relation: string | null;
          confidence: string | null;
        }>;
      };
      inference_strategy?: string;
      inference_steps?: Array<{ label: string; tokens: { input?: number; output?: number } }>;
      web_sources?: Array<{ title: string; url: string }>;
      // What the brain doesn't know yet — populated from the synth's
      // <gaps>…</gaps> block. Empty when the answer was well-grounded.
      gaps?: string[];
      memory_used?: boolean;
    };

export type Conversation = {
  id: string;
  title: string;
  intent?: string | null;
  rubric_id?: string | null;
  inference_strategy?: string;
  web_grounding?: boolean;
  auto_memory?: boolean;
  answer_model?: string | null;
  created_at: number;
  updated_at: number;
  turns: Turn[];
  pins: Pin[];
};

export type ModelOption = {
  id: string;
  label: string;
  hint?: string;
};

export type ForesightPersona = {
  id: string;
  source: "preset" | "customized" | "custom";
  label: string;
  tagline?: string;
  system: string;
  color?: string;
  key?: string;
  created_at?: number;
  updated_at?: number;
};

export type ForesightRoundEntry = {
  persona_id: string;
  label: string;
  color?: string;
  text: string;
  tokens?: { input?: number; output?: number };
};

export type ForesightOutput = {
  rounds: ForesightRoundEntry[][];
  synthesis: string;
  tokens: { input: number; output: number };
  elapsed_ms: number;
  entry_node_labels?: string[];
  web_sources?: Array<{ title: string; url: string }>;
};

export type ForesightSession = {
  id: string;
  title: string;
  scenario: string;
  horizon: string;
  rounds: number;
  world_context: string;
  personas: string[];
  rubric_id?: string | null;
  use_graph: boolean;
  answer_model?: string | null;
  source_conversation_id?: string | null;
  source_conversation_title?: string | null;
  use_memory?: boolean;
  web_grounding?: boolean;
  synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
  status: "draft" | "running" | "complete";
  output?:
    | (ForesightOutput & {
        used_memory?: boolean;
        used_conversation_history?: boolean;
        used_web_grounding?: boolean;
        used_intent?: boolean;
        synth_inference_strategy?: "none" | "reflection" | "cove" | "best_of_3";
        synth_inference_steps?: Array<{ label: string; tokens: { input?: number; output?: number } }>;
      })
    | null;
  created_at: number;
  updated_at: number;
};

export type ForesightSessionSummary = {
  id: string;
  title: string;
  scenario: string;
  horizon: string;
  horizon_label: string;
  persona_count: number;
  rounds: number;
  status: "draft" | "running" | "complete";
  created_at: number;
  updated_at: number;
};

export type RubricSource = "builtin" | "customized" | "user";

export type Rubric = {
  id: string;
  name: string;
  body: string;
  source?: RubricSource;
  created_at?: number;
  updated_at?: number;
};

export type AvailableRubric = {
  id: string;
  name: string;
  body: string;
  source: RubricSource;
  // workspace_id is null for built-in templates; otherwise the workspace
  // that holds this rubric. The picker shows where each entry comes from.
  workspace_id: string | null;
  workspace_name: string | null;
};

export type MemoryItem = {
  id: string;
  text: string;
  source: "manual" | "auto";
  tag?: string | null;
  created_at: number;
  updated_at: number;
};

export type RelinkResult = {
  edges_added: number;
  edges_before: number;
  edges_after: number;
  components_before: number;
  components_after: number;
  communities_after: number;
  pairs?: Array<{
    source: string;
    target: string;
    source_label: string;
    target_label: string;
    relation: string;
    confidence_score: number;
    reason?: string;
  }>;
  meta?: { input_tokens?: number; output_tokens?: number; model?: string; error?: string };
  error?: string;
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export type KBRefinementKind = "correction" | "addition" | "attestation" | "dissent";
export type KBSourceType = "human" | "document" | "web" | "kb_audit";
export type KBConfidence = "high" | "medium" | "low";

export type KBCorrection = {
  id: string;
  kind: KBRefinementKind;
  target_node_id: string | null;
  target_edge_id: string | null;
  source_type: KBSourceType;
  author: string;
  author_basis: string;
  confidence: KBConfidence;
  original_summary: string;
  new_summary: string;
  reason: string;
  evidence_url: string | null;
  created_at: number;
  updated_at: number;
};

export type KBNodeMatch = {
  id: string;
  label: string;
  source_file?: string | null;
  community_label?: string | null;
  // Deterministic-pass type — one of {"person","company","organization","product"}
  // or null when no heuristic matched.
  entity_type?: string | null;
  extracted_at?: number | null;
  refinement_counts?: Partial<Record<KBRefinementKind, number>>;
};

export type KBDiff = {
  added: Array<{ id: string; label?: string; source_file?: string | null }>;
  removed: Array<{ id: string; label?: string; source_file?: string | null }>;
  relabeled: Array<{ id: string; old: string; new: string }>;
  counts: {
    nodes_before: number;
    nodes_after: number;
    edges_before: number;
    edges_after: number;
  };
  computed_at?: number;
  kind?: string;
};

// Stable color per community id.
export function communityColor(id: number | null | undefined): string {
  if (id == null) return "#94a3b8";
  const palette = [
    "#818cf8", "#34d399", "#fb7185", "#fbbf24", "#22d3ee",
    "#a78bfa", "#f472b6", "#4ade80", "#fb923c", "#60a5fa",
    "#facc15", "#2dd4bf", "#e879f9", "#fb7185", "#a3e635",
  ];
  return palette[id % palette.length];
}
