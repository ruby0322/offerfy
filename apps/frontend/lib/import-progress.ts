/**
 * Pure reducer turning one import-run snapshot (polled or streamed over SSE)
 * into the view model the progress card renders from. No React, no fetch,
 * no DOM — this is the only part of the import-progress feature the repo's
 * `node:test` harness can exercise directly.
 */

export type ImportStepStatus = "pending" | "active" | "done" | "skipped";

export type ImportSnapshot = {
  import_id: string;
  status: "running" | "done" | "failed";
  version: number;
  applied: number;
  steps: {
    key: string;
    label: string;
    status: ImportStepStatus;
    detail: string | null;
  }[];
  error: string | null;
  started_at: string;
  stale: boolean;
};

export type ImportViewStep = {
  key: string;
  label: string;
  status: ImportStepStatus;
  detail: string | null;
  unlabelled: boolean;
};

export type ImportView = {
  mode: "running" | "done" | "failed" | "stale";
  steps: ImportViewStep[];
  doneCount: number;
  totalCount: number;
  error: string | null;
  showPlan: boolean;
};

export function toImportView(snap: ImportSnapshot): ImportView {
  const mode = snap.status === "running" && snap.stale ? "stale" : snap.status;

  const steps: ImportViewStep[] = snap.steps.map((step) => ({
    key: step.key,
    label: step.label,
    status: step.status,
    detail: step.detail,
    unlabelled: step.label === "",
  }));

  const doneCount = steps.filter((step) => step.status === "done").length;

  return {
    mode,
    steps,
    doneCount,
    totalCount: steps.length,
    error: snap.error,
    showPlan: mode !== "done",
  };
}

/**
 * The i18n key for an import's stored error code, or null if there is none.
 *
 * `resume_imports.error` holds a code (see `import_runner.py`'s
 * `UPLOAD_MISSING` / `UNEXPECTED`), not a sentence: the card renders in three
 * languages, and the raw text it used to show was both untranslated and a
 * leak -- `could not read the uploaded file
 * ('uploads/<owner>/<resume>/<name>.pdf')` published our storage-key shape to
 * whoever hit it. An unrecognised code falls back to the generic key rather
 * than being shown as-is, so a code added on the server can never surface as
 * bare text here.
 */
export function importErrorKey(error: string | null): ImportErrorKey | null {
  if (!error) return null;
  return IMPORT_ERROR_KEYS[error] ?? "importErrorUnexpected";
}

/** A literal union, not `string`: next-intl types `t()` against the message
 *  catalogue, so a widened return here would not be callable with it. */
export type ImportErrorKey = "importErrorUploadMissing" | "importErrorUnexpected";

const IMPORT_ERROR_KEYS: Record<string, ImportErrorKey> = {
  upload_missing: "importErrorUploadMissing",
  unexpected: "importErrorUnexpected",
};

export function importTurnStartsAt(snap: ImportSnapshot): number {
  return Date.parse(snap.started_at);
}

/** Just enough of a chat message for the grouping below. */
export type ImportTurnMessage = {
  role: string;
  /** Aware ISO, from the server. */
  created_at?: string;
  /** Aware ISO, for a message this client appended optimistically. */
  timestamp?: string;
};

function messageTime(message: ImportTurnMessage): number | null {
  const raw = message.created_at ?? message.timestamp;
  if (!raw) return null;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * How many messages come before the import's progress card — i.e. where the
 * import's own turn ends.
 *
 * An import writes no `user` row: the prompt is server-owned, so the first
 * `user` message at or after the run started is the next turn, and the card
 * belongs immediately above it. While the import is the last thing that
 * happened there is no such message and the card is last in the transcript,
 * which is the in-flight case — tool messages keep appending above it.
 *
 * This is what stops a finished import's collapsed card staying pinned below
 * every later message, on this visit and every future one: `getImport` keeps
 * returning the last run's snapshot forever, so without the grouping the card
 * reads as a UI artifact stuck to the bottom of the pane.
 *
 * A message with no usable timestamp can never be the boundary. That is the
 * degraded case (an older server, or a proxy that dropped the field), and it
 * lands on "card last", which is where it was before any grouping existed.
 */
export function importCardIndex(
  messages: ImportTurnMessage[],
  turnStartsAt: number,
): number {
  if (!Number.isFinite(turnStartsAt)) return messages.length;
  for (let i = 0; i < messages.length; i += 1) {
    if (messages[i].role !== "user") continue;
    const at = messageTime(messages[i]);
    if (at !== null && at >= turnStartsAt) return i;
  }
  return messages.length;
}

/**
 * Whether this snapshot means the resume's source on the server has moved on
 * from the copy the editor is holding, so the editor must adopt the server's.
 *
 * `seenApplied` is the `applied` count of the last snapshot already accounted
 * for. A higher one means another section landed.
 *
 * `done` counts as changed on its own, whatever `applied` says. The run's last
 * edit can land in the same write that ends the run, and a stream that
 * reconnects or drops a frame can miss the growth entirely -- and the cost of
 * being wrong is asymmetric: one redundant fetch against an import that is
 * silently replaced by whatever stale source the editor still holds (see
 * `EditorShell`'s `onImportSnapshot`).
 */
export function importSourceChanged(snap: ImportSnapshot, seenApplied: number): boolean {
  return snap.applied > seenApplied || snap.status === "done";
}
