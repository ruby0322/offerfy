/**
 * Pure reducer turning one import-run snapshot (polled or streamed over SSE)
 * into the view model the progress card renders from. No React, no fetch,
 * no DOM — this is the only part of the import-progress feature the repo's
 * `node:test` harness can exercise directly.
 */

export type ImportStepStatus = "pending" | "active" | "done" | "skipped";

/**
 * The reserved step key the backend writes for its own render-verification
 * step (`app.services.import_run.set_render_check`) -- never a key the
 * model declared or named. The model's `declare_import_plan` and
 * `apply_typst_edit`'s `step` argument are both sanitised server-side to
 * refuse this exact string (see `import_run.py`'s `_sanitise_plan` and
 * `advance`), so a step carrying this key is always the server-owned one,
 * never a coincidence.
 *
 * This takes precedence over the plain `unlabelled` rule below: the
 * backend writes this step with an empty `label` on purpose (the
 * translated text lives here, in `ImportProgressCard.tsx`, not on the
 * server), and the generic "unnamed edit" wording `unlabelled` triggers
 * would be wrong for it.
 */
export const RENDER_CHECK_KEY = "__render_check";

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
  /** This is the server-owned render-check step (see `RENDER_CHECK_KEY`),
   *  not a plan step the model declared. */
  renderCheck: boolean;
  /** Only meaningful when `renderCheck` is true: the check is mid-repair
   *  rather than mid-verify. `set_render_check` writes this as
   *  `status="active", detail="repairing"` -- there is no dedicated schema
   *  value for it (see that function's docstring for why). */
  repairing: boolean;
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

  const steps: ImportViewStep[] = snap.steps.map((step) => {
    const renderCheck = step.key === RENDER_CHECK_KEY;
    return {
      key: step.key,
      label: step.label,
      status: step.status,
      detail: step.detail,
      // The render-check step's empty label is deliberate (see
      // RENDER_CHECK_KEY) and must never fall into the generic
      // "unnamed edit" rendering `unlabelled` triggers for everything else.
      unlabelled: step.label === "" && !renderCheck,
      renderCheck,
      repairing: renderCheck && step.detail === "repairing",
    };
  });

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
export type ImportErrorKey =
  | "importErrorUploadMissing"
  | "importErrorUnexpected"
  | "importErrorRenderCheckFailed";

const IMPORT_ERROR_KEYS: Record<string, ImportErrorKey> = {
  upload_missing: "importErrorUploadMissing",
  unexpected: "importErrorUnexpected",
  // `import_runner.RENDER_CHECK_FAILED` -- `verify_render` was still not
  // `ok` after the gate's own repair attempts ran out.
  render_check_failed: "importErrorRenderCheckFailed",
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

/**
 * How much of an import run this client has taken in, and whether its buffer
 * is currently known to be behind the server.
 *
 * `outstanding` is the load-bearing field. An import rewrites
 * `resume.typst_source` server-side; between deciding to adopt that source
 * and the adopt actually landing in the editor, the editor is holding an
 * older document than the server has -- and every save path PUTs what it
 * holds. So the flag goes up when the adopt is *decided*, not when it starts
 * succeeding, and comes down only once the new source has landed.
 */
export type ImportAdoptCursor = {
  /** `applied` of the last snapshot whose source landed in the editor. */
  applied: number;
  /** `version` of the last snapshot whose transcript was read back. */
  version: number;
  /** A decided adopt that has not landed: the buffer is behind the server. */
  outstanding: boolean;
};

export type ImportAdoptPlan = {
  /** Re-read the resume and replace the editor's `source` with it. */
  adoptSource: boolean;
  /** Re-read the chat transcript: the runner has written rows since. */
  readTranscript: boolean;
};

/** A cursor for a run nothing has been taken in from yet. */
export function emptyImportCursor(): ImportAdoptCursor {
  return { applied: 0, version: 0, outstanding: false };
}

/**
 * A cursor seeded from a snapshot the client has *already* accounted for --
 * the mount, which loaded the source and the transcript itself, and a freshly
 * started run, whose snapshot predates any write.
 */
export function seedImportCursor(snap: ImportSnapshot): ImportAdoptCursor {
  return { applied: snap.applied, version: snap.version, outstanding: false };
}

/**
 * What one snapshot asks the editor to do.
 *
 * `adoptSource` stays true while an adopt is outstanding, whatever this
 * snapshot says. Without that, a single failed `getResume` is permanent: the
 * next snapshot carries the same `applied`, `importSourceChanged` returns
 * false, and the editor keeps serving the pre-import source to every save
 * path for the rest of the session.
 */
export function planImportAdopt(
  snap: ImportSnapshot,
  cursor: ImportAdoptCursor,
): ImportAdoptPlan {
  return {
    adoptSource: cursor.outstanding || importSourceChanged(snap, cursor.applied),
    readTranscript: snap.version > cursor.version,
  };
}

export type ImportAdoptOutcome =
  /** `setSource` has run with the server's copy. */
  | "adopted"
  /** The re-read failed; the buffer is still the old one. */
  | "failed"
  /** This snapshot asked for no adopt. */
  | "not-needed";

/**
 * The cursor after acting on `snap`.
 *
 * `applied` advances **only** on a successful adopt, so a failed one is
 * retried by the next snapshot through both halves of `planImportAdopt`'s
 * disjunction rather than neither. `version` advances either way: a missed
 * transcript re-read costs the person a stale message list until the next
 * frame or a reload, which is not the resume.
 */
export function nextImportCursor(
  snap: ImportSnapshot,
  cursor: ImportAdoptCursor,
  outcome: ImportAdoptOutcome,
): ImportAdoptCursor {
  const version = Math.max(cursor.version, snap.version);
  if (outcome === "failed") {
    return { applied: cursor.applied, version, outstanding: true };
  }
  return {
    applied: Math.max(cursor.applied, snap.applied),
    version,
    outstanding: false,
  };
}

/**
 * Whether an import forbids PUTting the editor's `source` back right now.
 *
 * Two reasons, and both are needed:
 *
 * - **A live run.** The agent is rewriting the resume section by section and
 *   our copy is only as fresh as the last snapshot we adopted; a write from
 *   here lands inside `set_draft_source`'s 120s coalesce window and replaces
 *   the import's own revision, so history cannot recover it either.
 * - **An outstanding adopt.** `mode` leaves `running` the moment the terminal
 *   snapshot arrives, which is one or two round trips before that snapshot's
 *   source has been adopted -- and if the adopt failed it never will be. The
 *   run being over is not evidence that our buffer caught up.
 *
 * `stale` and `failed` runs are deliberately *not* blocked by mode alone:
 * `advance()` bumps `applied` before `set_draft_source` commits, so a dead
 * run can legitimately sit one section ahead, and the adopt is what settles
 * it. Blocking on the cursor covers that; blocking on the mode would refuse
 * to save for as long as the card is on screen.
 */
export function importBlocksSave(
  mode: ImportView["mode"] | undefined,
  cursor: ImportAdoptCursor,
): boolean {
  return mode === "running" || cursor.outstanding;
}
