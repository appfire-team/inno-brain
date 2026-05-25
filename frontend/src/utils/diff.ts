// Word-level diff via classic LCS. Tokens preserve whitespace so the rendered
// diff keeps line breaks and spacing intact. No external deps.

export type DiffOp = { type: "equal" | "add" | "remove"; text: string };

function tokenize(s: string): string[] {
  // Split on whitespace boundaries while keeping the whitespace as tokens.
  // Example: "a  b\nc" -> ["a", "  ", "b", "\n", "c"]
  if (!s) return [];
  const out: string[] = [];
  const re = /(\s+|[^\s]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) out.push(m[0]);
  return out;
}

export function wordDiff(oldText: string, newText: string): DiffOp[] {
  const a = tokenize(oldText);
  const b = tokenize(newText);
  const n = a.length;
  const m = b.length;

  // Fast paths
  if (n === 0 && m === 0) return [];
  if (n === 0) return [{ type: "add", text: newText }];
  if (m === 0) return [{ type: "remove", text: oldText }];
  if (oldText === newText) return [{ type: "equal", text: oldText }];

  // LCS table — int matrix, but flatten with Uint32Array for speed.
  // Length-limited safety: bail to a coarse line-level diff if too large.
  if (n * m > 4_000_000) {
    return coarseLineDiff(oldText, newText);
  }
  const lcs = new Uint32Array((n + 1) * (m + 1));
  const w = m + 1;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) lcs[i * w + j] = lcs[(i - 1) * w + (j - 1)] + 1;
      else lcs[i * w + j] = Math.max(lcs[(i - 1) * w + j], lcs[i * w + (j - 1)]);
    }
  }

  // Backtrack to produce ops in forward order.
  const ops: DiffOp[] = [];
  let i = n, j = m;
  const push = (type: DiffOp["type"], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text;
    else ops.push({ type, text });
  };
  // Backtrack into a reverse list, then reverse at the end.
  const rev: DiffOp[] = [];
  const pushRev = (type: DiffOp["type"], text: string) => {
    const last = rev[rev.length - 1];
    if (last && last.type === type) last.text = text + last.text;
    else rev.push({ type, text });
  };
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      pushRev("equal", a[i - 1]);
      i--; j--;
    } else if (lcs[(i - 1) * w + j] >= lcs[i * w + (j - 1)]) {
      pushRev("remove", a[i - 1]);
      i--;
    } else {
      pushRev("add", b[j - 1]);
      j--;
    }
  }
  while (i > 0) { pushRev("remove", a[i - 1]); i--; }
  while (j > 0) { pushRev("add", b[j - 1]); j--; }

  for (let k = rev.length - 1; k >= 0; k--) push(rev[k].type, rev[k].text);
  return ops;
}

// Fallback for absurdly large inputs: diff line-by-line instead of word-by-word.
function coarseLineDiff(oldText: string, newText: string): DiffOp[] {
  const a = oldText.split(/(\n)/g);
  const b = newText.split(/(\n)/g);
  const n = a.length, m = b.length;
  const lcs = new Uint32Array((n + 1) * (m + 1));
  const w = m + 1;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) lcs[i * w + j] = lcs[(i - 1) * w + (j - 1)] + 1;
      else lcs[i * w + j] = Math.max(lcs[(i - 1) * w + j], lcs[i * w + (j - 1)]);
    }
  }
  const rev: DiffOp[] = [];
  const pushRev = (type: DiffOp["type"], text: string) => {
    const last = rev[rev.length - 1];
    if (last && last.type === type) last.text = text + last.text;
    else rev.push({ type, text });
  };
  let i = n, j = m;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) { pushRev("equal", a[i - 1]); i--; j--; }
    else if (lcs[(i - 1) * w + j] >= lcs[i * w + (j - 1)]) { pushRev("remove", a[i - 1]); i--; }
    else { pushRev("add", b[j - 1]); j--; }
  }
  while (i > 0) { pushRev("remove", a[i - 1]); i--; }
  while (j > 0) { pushRev("add", b[j - 1]); j--; }
  const ops: DiffOp[] = [];
  const push = (type: DiffOp["type"], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text;
    else ops.push({ type, text });
  };
  for (let k = rev.length - 1; k >= 0; k--) push(rev[k].type, rev[k].text);
  return ops;
}
