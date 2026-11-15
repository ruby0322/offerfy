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
export type TailorRunPhase = "reading" | "assessing" | "writing";

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
};

export function toTailorRunView(snap: TailorRunSnapshot): TailorRunView {
  // `stale` first: a dead runner's row still reads `running`.
  const mode: TailorRunMode = snap.stale
    ? "stale"
    : snap.status === "running"
      ? "running"
      : snap.status === "done"
        ? "done"
        : "failed";
  return {
    mode,
    // A finished run has no current phase, and a stale one's last phase is a
    // claim about what it was doing when it died -- true, and the only thing
    // there is to say, so it is kept.
    phase: mode === "done" ? null : snap.phase,
    job: snap.job,
    jobId: snap.job?.job_id ?? snap.job_id,
    error: snap.error,
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
