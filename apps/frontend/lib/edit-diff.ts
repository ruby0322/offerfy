export type DiffOp = "add" | "del";

export type DiffLine = {
  op: DiffOp;
  text: string;
};

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function splitLines(value: string): string[] {
  return value.split("\n");
}

/** Line LCS; equal lines are omitted so the card stays a +/- hunk. */
export function lineDiff(oldText: string, newText: string, limit = 80): DiffLine[] {
  const oldLines = splitLines(oldText);
  const newLines = splitLines(newText);
  const n = oldLines.length;
  const m = newLines.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        oldLines[i] === newLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m && rows.length < limit) {
    if (oldLines[i] === newLines[j]) {
      i += 1;
      j += 1;
      continue;
    }
    if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ op: "del", text: oldLines[i] });
      i += 1;
    } else {
      rows.push({ op: "add", text: newLines[j] });
      j += 1;
    }
  }
  while (i < n && rows.length < limit) {
    rows.push({ op: "del", text: oldLines[i] });
    i += 1;
  }
  while (j < m && rows.length < limit) {
    rows.push({ op: "add", text: newLines[j] });
    j += 1;
  }
  return rows;
}

function fromStored(raw: unknown): DiffLine[] | null {
  if (!Array.isArray(raw)) return null;
  const rows: DiffLine[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const op = (entry as { op?: unknown }).op;
    const text = (entry as { text?: unknown }).text;
    if ((op === "add" || op === "del") && typeof text === "string") {
      rows.push({ op, text });
    }
  }
  return rows;
}

export function diffFromEditPayload(payload: {
  arguments: Record<string, unknown>;
  result: unknown;
}): DiffLine[] {
  const stored =
    payload.result && typeof payload.result === "object"
      ? fromStored((payload.result as { diff?: unknown }).diff)
      : null;
  if (stored && stored.length > 0) return stored;
  const hasSource = asString(payload.arguments.source);
  const search = asString(payload.arguments.search);
  const replace = asString(payload.arguments.replace);
  if (!hasSource && search && replace) return lineDiff(search, replace);
  const replacement = asString(payload.arguments.replacement);
  if (replacement != null) {
    return splitLines(replacement).map((text) => ({ op: "add" as const, text }));
  }
  return [];
}
