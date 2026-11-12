import type { Application } from "@/lib/applications";

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
