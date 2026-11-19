import type { MatchArrival } from "@/lib/dashboard";
import { STRONG_TIER } from "@/lib/digest";

/**
 * Pure helpers for the arrivals block and the page's header line.
 *
 * An arrival is a posting scored for this person since they last looked,
 * measured by the server. Nothing here asks the browser what it remembers.
 *
 * `groupArrivals` and its Taipei-day helper lived here too, for the separate
 * arrivals card. That card is gone -- the overview is one card with two
 * columns again -- and nothing groups by day any more, so they went with it.
 */

export type ArrivalHeadline = {
  /**
   * first_visit: the server has never recorded a look -- a fact of the
   * account, not of this browser, so it is never the "cleared storage" case.
   */
  kind: "first_visit" | "nothing_new" | "arrivals";
  count: number;
};

export function arrivalHeadline(count: number, firstVisit: boolean): ArrivalHeadline {
  if (firstVisit) return { kind: "first_visit", count };
  if (count === 0) return { kind: "nothing_new", count: 0 };
  return { kind: "arrivals", count };
}

/**
 * What to say about this row's score. Three states, three claims:
 *
 * - scored: a score is present; show it.
 * - pending: no score row exists yet (`scored_at` is the row's own timestamp,
 *   so null means no row). A score is coming: "still scoring".
 * - unscored: a row exists but carries no score -- the model looked and could
 *   not judge it. Say nothing about scoring; it is never coming.
 *
 * MatchArrivalOut does not carry `outcome`, so every "row exists, no score"
 * case is one state here. If a prose-rejected row ever needs different
 * wording, the schema has to carry the outcome; this must not guess further.
 */
export function arrivalState(item: MatchArrival): "scored" | "pending" | "unscored" {
  if (item.fit_score !== null) return "scored";
  return item.scored_at === null ? "pending" : "unscored";
}

/** Distinct postings among the arrivals the model rated "Strong fit". */
export function strongArrivalCount(items: MatchArrival[]): number {
  const ids = new Set<string>();
  for (const it of items) if (it.tier === STRONG_TIER) ids.add(it.job_id);
  return ids.size;
}
