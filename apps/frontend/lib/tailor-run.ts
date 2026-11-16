/**
 * The tailor pass, as the editor reads it.
 *
 * A tailor resume's first pass used to be a chat message the browser sent, so
 * the canned instruction was persisted as the person's own words and a reload
 * mid-pass lost the work with nothing that would retry it. It is a durable
 * server-side run now (`app/services/tailor_run.py`), and this module is the
 * client's view of its snapshot.
 *
 * `mode` is the one thing the card switches on, and it is derived rather than
 * read straight from `status`, because `stale` outranks `running`: a run whose
 * worker died still says `running` in the database -- nothing reaps it -- and
 * rendering that as "working" would spin forever.
 */

export type TailorRunStatus = "running" | "done" | "failed";

/**
 * The pass's four stages, in order, each one entered by the tool that does
 * it: read the resume, read the posting, map the posting's requirements
 * against it, propose changes.
 *
 * A constant here rather than labels from the server, the same division the
 * import's reserved render-check step draws: the server owns which stage a
 * run reached, the card owns what that stage is called, so the four names are
 * translated like every other string in the editor.
 */
export const TAILOR_STEPS = ["read_resume", "read_job", "mapping", "proposing"] as const;

export type TailorStepKey = (typeof TAILOR_STEPS)[number];

/**
 * What `phase` can hold. The last three are the vocabulary this table
 * shipped with: only `finish`/`fail` clear the column and a stale run keeps
 * its phase forever, so a run that outlives a deploy still carries one.
 */
export type TailorRunPhase = TailorStepKey | "reading" | "assessing" | "writing";

export type TailorStepStatus = "pending" | "active" | "done" | "skipped";

export type TailorViewStep = { key: TailorStepKey; status: TailorStepStatus };

/**
 * How far along `phase` says the pass is, as an index into `TAILOR_STEPS`,
 * or null when it names no stage this card can place.
 *
 * The legacy names map to the stage they were nearest to, so a pass in flight
 * across a deploy draws a sensible list instead of an empty one. `writing`
 * has no stage at all now -- the pass is forbidden to edit -- so it maps to
 * the last one: a run that got as far as editing had certainly read both
 * documents.
 */
export function tailorStepIndex(phase: string | null | undefined): number | null {
  switch (phase) {
    case "read_resume":
    case "reading":
      return 0;
    case "read_job":
      return 1;
    case "mapping":
    case "assessing":
      return 2;
    case "proposing":
    case "writing":
      return 3;
    default:
      return null;
  }
}

export type TailorRunJob = {
  job_id: string;
  title: string;
  company: string;
  location: string | null;
  is_active: boolean;
};

export type TailorRunSnapshot = {
  run_id: string;
  status: TailorRunStatus;
  phase: TailorRunPhase | null;
  error: string | null;
  version: number;
  stale: boolean;
  created_at: string;
  job_id: string | null;
  job: TailorRunJob | null;
  source_hash: string | null;
  /**
   * How many chat rows this resume has, as of this frame.
   *
   * The signal for "there is something new to read", and deliberately not
   * `version` -- see `tailorTranscriptMoved`. Optional on the wire so a
   * frontend ahead of the API still parses.
   */
  message_count?: number | null;
};

export function isTailorRunSnapshot(value: unknown): value is TailorRunSnapshot {
  if (!value || typeof value !== "object") return false;
  const rec = value as Record<string, unknown>;
  return (
    typeof rec.run_id === "string" &&
    typeof rec.status === "string" &&
    typeof rec.version === "number" &&
    typeof rec.stale === "boolean"
  );
}

/** What the card is: working, finished, interrupted, or broken. */
export type TailorRunMode = "running" | "done" | "stale" | "failed";

export type TailorRunView = {
  mode: TailorRunMode;
  phase: TailorRunPhase | null;
  job: TailorRunJob | null;
  /** Present only so a card with no job row can still link to the posting. */
  jobId: string | null;
  error: string | null;
  /** The fixed four, each resolved against the stage the pass reached. */
  steps: TailorViewStep[];
  doneCount: number;
  totalCount: number;
};

/**
 * The four steps, given the stage a pass reached and whether it is still
 * going.
 *
 * While running: everything before the cursor is done, the cursor is active,
 * the rest are pending. Once it is over the rest are **skipped**, not
 * pending, and that distinction is the whole reason the server stopped
 * clearing `phase` on a terminal row -- a model that skipped a tool would
 * otherwise leave a finished card showing four ticks for work that never
 * happened.
 *
 * With no placeable phase at all (an older row, or a column this card does
 * not know) nothing is claimed: pending throughout while running, skipped
 * throughout once over.
 */
function stepsFor(mode: TailorRunMode, phase: string | null): TailorViewStep[] {
  const cursor = tailorStepIndex(phase);
  const running = mode === "running";
  return TAILOR_STEPS.map((key, index) => {
    if (cursor === null) return { key, status: running ? "pending" : "skipped" };
    if (index < cursor) return { key, status: "done" };
    if (index === cursor) return { key, status: running ? "active" : "done" };
    return { key, status: running ? "pending" : "skipped" };
  });
}

export function toTailorRunView(snap: TailorRunSnapshot): TailorRunView {
  // `stale` first: a dead runner's row still reads `running`.
  const mode: TailorRunMode = snap.stale
    ? "stale"
    : snap.status === "running"
      ? "running"
      : snap.status === "done"
        ? "done"
        : "failed";
  const steps = stepsFor(mode, snap.phase);
  return {
    mode,
    // On a terminal row `phase` is the furthest stage reached rather than a
    // current one, which is exactly what the step list needs -- so it is kept
    // in every mode now, and the card reads `steps`, not this.
    phase: snap.phase,
    job: snap.job,
    jobId: snap.job?.job_id ?? snap.job_id,
    error: snap.error,
    steps,
    doneCount: steps.filter((step) => step.status === "done").length,
    totalCount: steps.length,
  };
}

/**
 * Whether the editor should offer to start a pass at all.
 *
 * Only for a tailor, only when no run has ever been recorded, and only while
 * the chat is empty. The empty chat is what makes this idempotent across a
 * reload, the same flag the old frontend kickstart used -- but it is no longer
 * the *only* guard, which is what made that version unrecoverable: the run row
 * is now the durable record, so a pass that already happened is known even
 * though its messages are there.
 */
export function shouldStartTailorRun(args: {
  kind: string | null | undefined;
  loaded: boolean;
  messageCount: number;
  snapshot: TailorRunSnapshot | null;
}): boolean {
  if (!args.loaded || args.kind !== "tailor") return false;
  if (args.snapshot !== null) return false;
  return args.messageCount === 0;
}

/**
 * Whether the composer must stay shut.
 *
 * The same rule the import has, for the same reason: a turn started while the
 * pass is still running is two agents editing one resume, both writing through
 * `set_draft_source`, inside whose coalesce window the loser's edits vanish
 * with no revision to recover them. A stale pass does NOT block -- its runner
 * is gone, so there is nothing to collide with.
 */
export function tailorRunBlocksComposer(view: TailorRunView | undefined): boolean {
  return view?.mode === "running";
}

/**
 * How much of a tailor pass this client has taken in, and whether its buffer
 * is currently known to be behind the server.
 *
 * The same shape and the same reasoning as `ImportAdoptCursor`, minus
 * `applied`: `tailor_runs` has no such column, because a pass does not edit
 * the resume section by section. `source_hash` is therefore the whole source
 * contract here rather than one of two signals.
 *
 * `outstanding` is the load-bearing field, for the reason the import spells
 * out: between deciding to adopt the server's source and that adopt landing,
 * the editor holds an older document than the server has -- and every save
 * path PUTs what it holds. The flag goes up when the adopt is *decided* and
 * comes down only once the new source has landed.
 */
export type TailorAdoptCursor = {
  /** `version` of the last snapshot accounted for; the fallback read signal. */
  version: number;
  /**
   * `message_count` of the last snapshot whose transcript was read back, or
   * null when the API does not send the field.
   */
  messageCount: number | null;
  /**
   * `source_hash` of the last snapshot whose source landed in the editor.
   *
   * `null` until the first successful adopt: the editor can read a source but
   * cannot hash it, so until it has adopted one *from a snapshot* it has no
   * hash it can honestly claim to be holding.
   */
  sourceHash: string | null;
  /** A decided adopt that has not landed: the buffer is behind the server. */
  outstanding: boolean;
};

export type TailorAdoptPlan = {
  /** Re-read the resume and replace the editor's `source` with it. */
  adoptSource: boolean;
  /** Re-read the chat transcript: the runner has written rows since. */
  readTranscript: boolean;
};

/** A cursor for a pass nothing has been taken in from yet. */
export function emptyTailorCursor(): TailorAdoptCursor {
  return { version: 0, messageCount: null, sourceHash: null, outstanding: false };
}

/**
 * A cursor seeded from a snapshot the client has *already* accounted for --
 * the mount, which loaded the source and the transcript itself, and a freshly
 * started pass, whose snapshot predates any write.
 *
 * `sourceHash` is deliberately left unseeded, exactly as `seedImportCursor`
 * leaves it: the mount loaded the source itself rather than adopting it from
 * a snapshot, and the two reads are separate round trips, so claiming this
 * snapshot's hash would be claiming the editor holds a source it may never
 * have seen.
 */
export function seedTailorCursor(snap: TailorRunSnapshot): TailorAdoptCursor {
  return {
    version: snap.version,
    messageCount: typeof snap.message_count === "number" ? snap.message_count : null,
    sourceHash: null,
    outstanding: false,
  };
}

/**
 * Whether the resume's source on the server is no longer what the editor
 * holds.
 *
 * A pass is not supposed to edit the resume at all -- it reads, maps the
 * posting's requirements and proposes changes, and the person applies the
 * ones they want. This still has to be checked rather than assumed: the edit
 * tool is in the model's toolset for the whole turn, so a pass that edits
 * anyway must not leave the preview, the PDF and the ATS report describing a
 * document nobody is looking at.
 *
 * With no hash on the wire at all (an API older than the field) the terminal
 * frame alone stands in, which costs one redundant `getResume` per pass.
 * There is no `applied` counter to fall back on the way the import has.
 */
export function tailorSourceChanged(
  snap: TailorRunSnapshot,
  cursor: TailorAdoptCursor,
): boolean {
  if (typeof snap.source_hash === "string") {
    if (cursor.sourceHash !== null) return snap.source_hash !== cursor.sourceHash;
    // Nothing adopted yet against a live pass: one adopt settles it and seeds
    // the hash for every frame after.
    if (snap.status === "running") return true;
  }
  return snap.status === "done";
}

/**
 * Whether the runner has written chat rows this client has not read.
 *
 * **`message_count`, not `version`** -- this is the whole reason the field
 * exists. `aiter_chat_edit` yields a `delta` event per streamed text chunk
 * (`app/services/llm/turn.py`), and `run_tailor` calls `touch()` on *every*
 * event, so `version` is bumped once per chunk: it is a liveness signal, not
 * a "there is something new to read" signal. Keyed on `version`, the stream's
 * 0.4s poll would re-read the entire transcript on every frame for the whole
 * pass -- hundreds of full fetches, and this feature is about to make those
 * rows bigger.
 *
 * `version` is still the fallback for a snapshot with no count on it, because
 * a stale message list is the bug this function was added to fix and missing
 * it entirely is worse than reading too often.
 */
export function tailorTranscriptMoved(
  snap: TailorRunSnapshot,
  cursor: TailorAdoptCursor,
): boolean {
  if (typeof snap.message_count === "number" && cursor.messageCount !== null) {
    return snap.message_count > cursor.messageCount;
  }
  return snap.version > cursor.version;
}

/**
 * What one snapshot asks the editor to do.
 *
 * `adoptSource` stays true while an adopt is outstanding, whatever this
 * snapshot says, so one failed `getResume` is retried rather than permanent.
 */
export function planTailorAdopt(
  snap: TailorRunSnapshot,
  cursor: TailorAdoptCursor,
): TailorAdoptPlan {
  return {
    adoptSource: cursor.outstanding || tailorSourceChanged(snap, cursor),
    readTranscript: tailorTranscriptMoved(snap, cursor),
  };
}

export type TailorAdoptOutcome =
  /** `setSource` has run with the server's copy. */
  | "adopted"
  /** The re-read failed; the buffer is still the old one. */
  | "failed"
  /** This snapshot asked for no adopt. */
  | "not-needed";

/**
 * The cursor after acting on `snap`.
 *
 * `version` and `messageCount` advance on every outcome: a missed transcript
 * re-read costs the person a stale message list until the next frame or a
 * reload, which is not the resume. `sourceHash` is the strict one -- only
 * `"adopted"` sets it, and only from the frame that was actually adopted,
 * because "this frame asked for nothing" is not evidence about what the
 * editor holds and recording it would silence the very next frame that does
 * carry a change.
 */
export function nextTailorCursor(
  snap: TailorRunSnapshot,
  cursor: TailorAdoptCursor,
  outcome: TailorAdoptOutcome,
): TailorAdoptCursor {
  const version = Math.max(cursor.version, snap.version);
  const messageCount =
    typeof snap.message_count === "number"
      ? Math.max(cursor.messageCount ?? 0, snap.message_count)
      : cursor.messageCount;
  if (outcome === "failed") {
    return { version, messageCount, sourceHash: cursor.sourceHash, outstanding: true };
  }
  return {
    version,
    messageCount,
    sourceHash:
      outcome === "adopted" && typeof snap.source_hash === "string"
        ? snap.source_hash
        : cursor.sourceHash,
    outstanding: false,
  };
}

/**
 * Whether a tailor pass forbids PUTting the editor's `source` back right now.
 *
 * The composer gate (`tailorRunBlocksComposer`) was never enough on its own:
 * it stops a typed turn, but the Typst tab's 400ms autosave and `flushSave`
 * are not turns, and they PUT whatever buffer this mount holds. While a pass
 * is running that buffer is only as fresh as the last snapshot adopted, and a
 * write from here lands inside `set_draft_source`'s coalesce window, so it
 * replaces the run's own revision with history unable to recover it.
 *
 * The outstanding clause covers the window the mode cannot: `mode` leaves
 * `running` the moment the terminal snapshot arrives, which is one or two
 * round trips before that snapshot's source has been adopted -- and if the
 * adopt failed, it never will be. A `stale` or `failed` pass is deliberately
 * not blocked by mode alone, for the reason `importBlocksSave` gives: its
 * runner is gone, and the cursor is what settles whether the buffer caught
 * up.
 */
export function tailorBlocksSave(
  mode: TailorRunMode | undefined,
  cursor: TailorAdoptCursor,
): boolean {
  return mode === "running" || cursor.outstanding;
}
