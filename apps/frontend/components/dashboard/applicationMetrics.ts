import type { Application } from "@/lib/applications";

/**
 * The five numbers the Applications page opens with.
 *
 * Every one of them is derived here, from the rows the dashboard response
 * already carries, so there is one definition of "a reply" on the page rather
 * than one per component. The rule throughout: a reply is `replied_at`, never
 * a status. Closing a row overwrites its status, so after the fact a rejection
 * and a silence are both `closed` and only the stamp tells them apart — see
 * app.models.Application.replied_at.
 *
 * `sent` counts rows with an `applied_at`, not rows currently at `applied`:
 * an application you have since heard back on was still sent. That is what
 * makes the denominator of the reply rate stable as rows move on.
 */
export type ApplicationMetrics = {
  /** Rows ever sent — `applied_at` present, whatever the row's status is now. */
  sent: number;
  /** Of those, how many the employer answered. */
  replied: number;
  /**
   * replied / sent as a percentage, rounded, or null when nothing has been
   * sent. Null rather than 0: "0%" from no applications reads as a verdict on
   * the person's resume, which is not a thing we know yet.
   */
  replyRate: number | null;
  /** Still at `applied`: sent, no outcome recorded. */
  waiting: number;
  /** Of those, how many have been quiet longer than QUIET_DAYS. */
  waitingTooLong: number;
  /** Tailored and never sent. */
  neverSent: number;
  /** The oldest never-sent row's age in days, or null when there are none. */
  neverSentOldestDays: number | null;
  /** Currently at `interviewing`. */
  interviewing: number;
  /**
   * Median days from sending to the reply, over rows that have both stamps,
   * or null when none do. Median, not mean: one application answered after
   * four months would drag a mean somewhere no individual case has ever been.
   */
  medianDaysToReply: number | null;
};

/** When an unanswered application counts as having gone quiet. */
export const QUIET_DAYS = 14;

const DAY_MS = 86_400_000;

function daysBetween(fromIso: string, to: Date): number | null {
  const from = Date.parse(fromIso);
  if (Number.isNaN(from)) return null;
  return Math.max(0, Math.floor((to.getTime() - from) / DAY_MS));
}

/** The median of a non-empty list; the mean of the middle two when even. */
export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid];
  return Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

export function applicationMetrics(
  rows: Application[],
  now: Date = new Date(),
): ApplicationMetrics {
  let sent = 0;
  let replied = 0;
  let waiting = 0;
  let waitingTooLong = 0;
  let interviewing = 0;
  const neverSentAges: number[] = [];
  const replyLags: number[] = [];

  for (const row of rows) {
    if (row.applied_at !== null) sent += 1;
    if (row.replied_at !== null) {
      replied += 1;
      // Only rows with both stamps can be timed. A row whose reply predates
      // the column, or which was stamped without ever being sent, is counted
      // as a reply above but cannot contribute a duration.
      if (row.applied_at !== null) {
        const from = Date.parse(row.applied_at);
        const to = Date.parse(row.replied_at);
        if (!Number.isNaN(from) && !Number.isNaN(to) && to >= from) {
          replyLags.push(Math.floor((to - from) / DAY_MS));
        }
      }
    }
    if (row.status === "applied") {
      waiting += 1;
      const quiet = daysBetween(row.last_status_at, now);
      if (quiet !== null && quiet > QUIET_DAYS) waitingTooLong += 1;
    }
    if (row.status === "interviewing") interviewing += 1;
    if (row.status === "tailored") {
      const age = daysBetween(row.last_status_at, now);
      if (age !== null) neverSentAges.push(age);
    }
  }

  return {
    sent,
    replied,
    replyRate: sent === 0 ? null : Math.round((replied / sent) * 100),
    waiting,
    waitingTooLong,
    neverSent: neverSentAges.length,
    neverSentOldestDays:
      neverSentAges.length === 0 ? null : Math.max(...neverSentAges),
    interviewing,
    medianDaysToReply: median(replyLags),
  };
}
