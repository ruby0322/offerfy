import type { Application } from "@/lib/applications";
// The quiet line is defined with the metrics that report it, so the table's
// sort and the "3 past 14 days" tile can never disagree about where it is.
import { QUIET_DAYS } from "./applicationMetrics";

export { QUIET_DAYS };

export type NextAction = "tailor" | "apply" | "logResponse" | "confirm";

/**
 * The one thing a row asks of the person. Closed asks nothing. An applied row
 * whose click was never confirmed asks "did you apply?" before anything else:
 * the outbound click alone does not prove it.
 */
export function nextAction(app: Application): NextAction | null {
  switch (app.status) {
    case "saved":
      return "tailor";
    case "tailored":
      return "apply";
    case "applied":
      return app.applied_confirmed_at === null ? "confirm" : "logResponse";
    case "interviewing":
      return "logResponse";
    default:
      return null;
  }
}

const DAY_MS = 86_400_000;

function daysSince(iso: string, now: Date): number {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return 0;
  return Math.max(0, Math.floor((now.getTime() - then) / DAY_MS));
}

/**
 * Days since the row last changed status. last_status_at moves only on a
 * status change (a notes edit does not), so this is real quiet time.
 */
export function quietDays(app: Application, now: Date = new Date()): number {
  return daysSince(app.last_status_at, now);
}

/** How long a tailored row has been ready to send; null for any other status. */
export function readyAgeDays(app: Application, now: Date = new Date()): number | null {
  return app.status === "tailored" ? daysSince(app.last_status_at, now) : null;
}

export type ResponseChoice = {
  key:
    | "responseHeardBack"
    | "responseSaidNo"
    | "responseNoAnswer"
    | "responseNotMoving";
  status: "interviewing" | "closed";
  /**
   * What to report about a reply, where the status cannot say it. Omitted
   * when the status implies it (interviewing) or when the row has already
   * recorded one (closing an interviewing row).
   */
  replied?: boolean;
};

/**
 * What "Log a response" offers once opened. It is the only writer of
 * `interviewing` and `closed`, and it never guesses.
 *
 * An applied row gets three choices, not two. "Not moving forward" was one
 * button covering two different facts — a rejection, where the employer did
 * answer, and silence, where they never did — and once the row is closed the
 * status cannot tell them apart afterwards. Reply rate and the pipeline
 * diagram both count replies, so the person is asked which it was, and the
 * answer rides along as `replied`.
 *
 * An interviewing row has already been recorded as having heard back, so it
 * only offers closing and says nothing about a reply.
 */
export function responseChoices(app: Application): ResponseChoice[] {
  if (app.status === "applied") {
    return [
      { key: "responseHeardBack", status: "interviewing" },
      { key: "responseSaidNo", status: "closed", replied: true },
      { key: "responseNoAnswer", status: "closed", replied: false },
    ];
  }
  if (app.status === "interviewing") {
    return [{ key: "responseNotMoving", status: "closed" }];
  }
  return [];
}

/**
 * How badly a row needs the person, lowest first.
 *
 * The table opens on this order rather than on status groups, because the
 * question someone brings to the page is "what do I do now?" and a status
 * group answers "what state is everything in?". It is the same ladder the
 * overview's next-action band uses, so the two never disagree about which
 * application is first.
 *
 * Ready-to-send leads: a tailored resume sitting unsent is work already paid
 * for and not yet spent. Then an application that has gone quiet long enough
 * to chase, then one whose outbound click was never confirmed -- one click,
 * but it is bookkeeping rather than progress. Everything past that is either
 * waiting on someone else or finished.
 */
export function urgencyRank(app: Application, now: Date = new Date()): number {
  switch (app.status) {
    case "tailored":
      return 0;
    case "applied":
      if (quietDays(app, now) > QUIET_DAYS) return 1;
      return app.applied_confirmed_at === null ? 2 : 4;
    case "interviewing":
      return 3;
    case "saved":
      return 5;
    default:
      return 6;
  }
}

/**
 * Rows in urgency order, and within a rank the most neglected first. Stable
 * on job_id so two rows changed in the same second do not swap on rerender.
 */
export function byUrgency(rows: Application[], now: Date = new Date()): Application[] {
  return [...rows].sort((a, b) => {
    const rank = urgencyRank(a, now) - urgencyRank(b, now);
    if (rank !== 0) return rank;
    const age = Date.parse(a.last_status_at) - Date.parse(b.last_status_at);
    if (!Number.isNaN(age) && age !== 0) return age;
    return a.job_id.localeCompare(b.job_id);
  });
}
