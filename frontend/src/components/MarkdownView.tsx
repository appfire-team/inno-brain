import { Fragment, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MermaidBlock } from "./MermaidBlock";

type Props = {
  children: string;
  className?: string;
};

// Tight, readable prose. We override a few elements so links open in a new tab,
// code/tables fit the dark theme, and inline citations render as visual chips.
export function MarkdownView({ children, className }: Props) {
  return (
    <div className={`md-prose ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer" />
          ),
          code: ({ inline, className: cn, children: c, ...props }: any) => {
            if (inline) {
              return <code className="md-inline-code" {...props}>{c}</code>;
            }
            const lang = /language-([\w-]+)/.exec(cn ?? "")?.[1];
            if (lang === "mermaid") {
              const source = Array.isArray(c) ? c.join("") : String(c ?? "");
              return <MermaidBlock code={source.replace(/\n$/, "")} />;
            }
            return <pre className="md-code-block"><code className={cn} {...props}>{c}</code></pre>;
          },
          table: ({ node: _node, ...props }) => (
            <div className="md-table-wrap"><table {...props} /></div>
          ),
          p: ({ node: _node, children: c }) => {
            const callout = detectCallout(c);
            if (callout) {
              return (
                <p className={`md-callout md-callout-${callout.tone}`}>
                  {callout.icon && <span className="md-callout-icon" aria-hidden>{callout.icon}</span>}
                  <span className="md-callout-body">{chipifyChildren(c)}</span>
                </p>
              );
            }
            return <p>{chipifyChildren(c)}</p>;
          },
          li: ({ node: _node, children: c, ...props }) => (
            <li {...props}>{chipifyChildren(c)}</li>
          ),
          em: ({ node: _node, children: c }) => <em>{chipifyChildren(c)}</em>,
          strong: ({ node: _node, children: c }) => <strong>{chipifyChildren(c)}</strong>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

// Inline-only markdown — renders bold/italic/links/code/citations without
// wrapping in a block element. Used for the lead sentence so it can still pick
// up chips and emphasis while sitting inside a styled <p>.
export function InlineMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ node: _node, children: c }) => <>{chipifyChildren(c)}</>,
        a: ({ node: _node, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer" />
        ),
        strong: ({ node: _node, children: c }) => <strong>{chipifyChildren(c)}</strong>,
        em: ({ node: _node, children: c }) => <em>{chipifyChildren(c)}</em>,
        code: ({ inline, children: c, ...props }: any) => (
          inline ? <code className="md-inline-code" {...props}>{c}</code> : <>{c}</>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}

// ---- Callout detection -----------------------------------------------------
// Lift the eye to load-bearing sentences. We scan only the very start of a
// paragraph (the leading text node) for canonical exec-brief phrases the LLM
// likes to use ("Bottom line:", "Key insight:", "Watch out:" …) and render the
// whole paragraph as a tinted callout block. Strictly visual — the prose text
// is preserved verbatim so old runs and conversations get the treatment too.

type CalloutTone = "key" | "info" | "warn" | "risk";

const CALLOUT_PATTERNS: Array<{ re: RegExp; tone: CalloutTone; icon: string }> = [
  {
    re: /^\s*(?:\*\*)?(bottom line|key insight|key takeaway|takeaway|tl;dr|in short|the answer|recommendation|verdict)(?:\*\*)?\s*[:—-]/i,
    tone: "key",
    icon: "★",
  },
  {
    re: /^\s*(?:\*\*)?(critically|importantly|note|nb|key point|worth noting)(?:\*\*)?\s*[:,]/i,
    tone: "info",
    icon: "ℹ",
  },
  {
    re: /^\s*(?:\*\*)?(however|but|on the other hand|counter|caveat)(?:\*\*)?\s*[:,]/i,
    tone: "info",
    icon: "↺",
  },
  {
    re: /^\s*(?:\*\*)?(watch out|caution|warning|be careful)(?:\*\*)?\s*[:,]/i,
    tone: "warn",
    icon: "⚠",
  },
  {
    re: /^\s*(?:\*\*)?(risk|risks|danger|threat|red flag)(?:\*\*)?\s*[:,]/i,
    tone: "risk",
    icon: "⚠",
  },
];

function detectCallout(children: ReactNode): { tone: CalloutTone; icon: string } | null {
  const first = firstTextOf(children);
  if (!first) return null;
  for (const p of CALLOUT_PATTERNS) {
    if (p.re.test(first)) return { tone: p.tone, icon: p.icon };
  }
  return null;
}

function firstTextOf(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) {
    for (const c of children) {
      const t = firstTextOf(c);
      if (t) return t;
    }
    return "";
  }
  // React element with props.children
  if (children && typeof children === "object" && "props" in (children as any)) {
    return firstTextOf((children as any).props?.children);
  }
  return "";
}

// ---- Citation chipifier ----------------------------------------------------
// Detects these patterns inside text nodes and turns them into chips:
//   (some_file.pdf)        — corpus citation (green)
//   (file1.pdf, file2.pdf) — multiple corpus chips
//   general knowledge:     — amber inline prefix chip
//   web: example.com       — blue web chip with optional URL link
//
// Other element children (bold/italic/links) are passed through untouched.

// Matches a parenthesized list of corpus filenames (.pdf/.md/.txt/.docx).
const CITATION_RE = /\(([^()]*?\.(?:pdf|md|txt|docx?|html?|csv|json))\)/gi;
// Matches "general knowledge:" or "general knowledge —" prefix.
const GENERAL_RE = /\b(general knowledge)\s*[:——-]/gi;
// Matches "web: <url-or-domain>" optionally inside parens.
const WEB_RE = /\(?\bweb:\s*(https?:\/\/\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:\/\S*)?)\)?/gi;

// Numeric/stat patterns the eye should snag on in an exec brief.
// Conservative: only the things that almost always read as load-bearing data.
const STAT_RES: RegExp[] = [
  // Currency: $123, $1,234, $1.2M, $1.2B, $10K, $250k
  /\$\d{1,3}(?:,\d{3})+(?:\.\d+)?[KMB]?\b|\$\d+(?:\.\d+)?[KMBkm]?\b/g,
  // Percentages: 12%, 12.5%, 12 %
  /\b\d+(?:\.\d+)?\s?%/g,
  // Person-weeks / person-months / engineer-weeks
  /\b\d+(?:\.\d+)?[-\s]?(?:person|engineer|eng|dev)[- ](?:week|month|day|year)s?\b/gi,
  // Quarters: Q3 2026, Q1'26
  /\bQ[1-4][ '-]?(?:20)?\d{2}\b/g,
  // ISO dates: 2026-05-20
  /\b\d{4}-\d{2}-\d{2}\b/g,
];

function chipifyChildren(children: ReactNode): ReactNode {
  if (typeof children === "string") return chipifyString(children);
  if (Array.isArray(children)) {
    return children.map((c, i) =>
      typeof c === "string" ? <Fragment key={i}>{chipifyString(c)}</Fragment> : c
    );
  }
  return children;
}

type Segment = string | { kind: "corpus" | "general" | "web" | "stat"; label: string; url?: string };

function chipifyString(text: string): ReactNode {
  const segments = splitIntoSegments(text);
  return segments.map((seg, i) => {
    if (typeof seg === "string") return <Fragment key={i}>{seg}</Fragment>;
    return <Citation key={i} {...seg} />;
  });
}

function splitIntoSegments(text: string): Segment[] {
  type Match = { start: number; end: number; seg: Exclude<Segment, string> };
  const matches: Match[] = [];

  // 1. Corpus citations like (file.pdf) or (a.pdf, b.pdf)
  for (const m of text.matchAll(CITATION_RE)) {
    const inner = m[1];
    const parts = inner.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
    if (parts.length === 1) {
      matches.push({
        start: m.index ?? 0,
        end: (m.index ?? 0) + m[0].length,
        seg: { kind: "corpus", label: parts[0] },
      });
    } else {
      // Multiple filenames inside one paren — emit each as its own chip but
      // collapse into one match span; we'll handle multi by encoding as label.
      matches.push({
        start: m.index ?? 0,
        end: (m.index ?? 0) + m[0].length,
        seg: { kind: "corpus", label: parts.join("; ") },
      });
    }
  }

  // 2. Web citations
  for (const m of text.matchAll(WEB_RE)) {
    const target = m[1];
    const url = target.startsWith("http") ? target : `https://${target}`;
    const domain = target.replace(/^https?:\/\//, "").split("/")[0];
    matches.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      seg: { kind: "web", label: domain, url },
    });
  }

  // 3. General-knowledge prefix
  for (const m of text.matchAll(GENERAL_RE)) {
    matches.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      seg: { kind: "general", label: "general knowledge" },
    });
  }

  // 4. Numeric stats — currency, percentages, person-weeks, dates.
  for (const re of STAT_RES) {
    for (const m of text.matchAll(re)) {
      matches.push({
        start: m.index ?? 0,
        end: (m.index ?? 0) + m[0].length,
        seg: { kind: "stat", label: m[0] },
      });
    }
  }

  if (matches.length === 0) return [text];

  // Sort and merge non-overlapping; on overlap keep the longest.
  matches.sort((a, b) => a.start - b.start || b.end - a.end);
  const filtered: Match[] = [];
  let cursor = 0;
  for (const m of matches) {
    if (m.start < cursor) continue;
    filtered.push(m);
    cursor = m.end;
  }

  const out: Segment[] = [];
  let pos = 0;
  for (const m of filtered) {
    if (m.start > pos) out.push(text.slice(pos, m.start));
    out.push(m.seg);
    pos = m.end;
  }
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}

function Citation({ kind, label, url }: Exclude<Segment, string>) {
  if (kind === "web" && url) {
    return (
      <a className={`cite cite-${kind}`} href={url} target="_blank" rel="noreferrer" title={url}>
        <span className="cite-icon" aria-hidden>🌐</span>
        <span className="cite-label">{label}</span>
      </a>
    );
  }
  if (kind === "corpus") {
    return (
      <span className={`cite cite-${kind}`} title={label}>
        <span className="cite-icon" aria-hidden>📄</span>
        <span className="cite-label">{shortFile(label)}</span>
      </span>
    );
  }
  if (kind === "stat") {
    // Inline accent, no icon or chip background — the eye should snag without
    // breaking the prose flow.
    return <span className="stat">{label}</span>;
  }
  return (
    <span className={`cite cite-${kind}`}>
      <span className="cite-label">{label}</span>
    </span>
  );
}

function shortFile(name: string): string {
  // If multiple files were joined with "; ", show first + "+N more"
  if (name.includes(";")) {
    const parts = name.split(";").map((s) => s.trim()).filter(Boolean);
    return `${trimFile(parts[0])} +${parts.length - 1}`;
  }
  return trimFile(name);
}

function trimFile(name: string, max = 32): string {
  if (name.length <= max) return name;
  // keep the extension visible
  const dot = name.lastIndexOf(".");
  if (dot < 0) return name.slice(0, max - 1) + "…";
  const ext = name.slice(dot);
  return name.slice(0, max - 1 - ext.length) + "…" + ext;
}
