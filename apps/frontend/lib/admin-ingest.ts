import type { AdminIngestHealth, AdminIngestPass } from "@/lib/api";

/**
 * The headline verdict for the ingest monitor page.
 *
 * - `empty`    nothing in the catalog at all
 * - `overdue`  no pass has refreshed anything within the expected window
 * - `expiring` fresh enough to serve, but some pages have already gone noindex
 * - `fresh`    every active posting is inside its validThrough window
 *
 * `expiring` is separate from `overdue` on purpose: a catalog can be refreshed
 * on schedule and still be shedding pages, because a posting only leaves the
 * index when its source drops it. Collapsing the two would hide that.
 */
export type IngestVerdict = "fresh" | "expiring" | "overdue" | "empty";

export function ingestVerdict(health: AdminIngestHealth): IngestVerdict {
  if (health.freshest_last_seen_at === null) return "empty";
  if (health.overdue) return "overdue";
  if (health.stale_job_count > 0) return "expiring";
  return "fresh";
}

export function isBadVerdict(verdict: IngestVerdict): boolean {
  return verdict === "overdue" || verdict === "empty";
}

/** Whole hours when it has been a while, one decimal when it is recent. */
export function formatAgeHours(hours: number | null): string {
  if (hours === null) return "—";
  if (hours >= 10) return `${Math.round(hours)}h`;
  return `${hours.toFixed(1)}h`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
}

/**
 * Boards that failed in the most recent pass.
 *
 * Reported off one pass rather than summed across passes: the same dead board
 * slug failing every 6 hours is one broken board, not N incidents.
 */
export function latestFailingBoards(passes: AdminIngestPass[]): AdminIngestPass | null {
  return passes.find((item) => item.failing_boards.length > 0) ?? null;
}
