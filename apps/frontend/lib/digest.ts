export type DigestStatus = "full" | "degraded" | "empty";
// The two real tracks. "unranked" (a degraded build) and any later track are
// plain strings: the API owns the vocabulary and this build must not drop one.
export type DigestTrack = "strong_fit" | "new_this_week" | (string & {});

export type DigestItem = {
  job_id: string;
  job_title: string;
  company: string;
  apply_url: string;
  job_is_active: boolean;
  track: DigestTrack;
  rank: number;
  tier: string | null;
  fit_score: number | null;
  reason: string | null;
  gap: string | null;
};

export type Digest = {
  status: DigestStatus;
  built_at: string;
  opened_at: string | null;
  candidate_count: number;
  items: DigestItem[];
};

// Same values, same order as the backend's TRACKS (services/jobs/digest.py).
export const TRACK_ORDER = ["strong_fit", "new_this_week"] as const;

// The backend's top tier, verbatim: STRONG_TIERS[0] in services/jobs/digest.py.
// Rename it there and here together; DigestCard's TIER_KEYS holds the same
// string for the label.
export const STRONG_TIER = "Strong fit";

/**
 * The items with one row per POSTING: the first row for each job_id, in the
 * order given.
 *
 * `items` is one row per (posting, track), so any caller treating it as a
 * list of roles sees a posting the build filed under both tracks twice. New
 * builds write the tracks disjoint, but a digest is frozen once written, so
 * the ones already built still need this.
 */
export function uniqueByJob(items: DigestItem[]): DigestItem[] {
  const seen = new Set<string>();
  const out: DigestItem[] = [];
  for (const it of items) {
    if (seen.has(it.job_id)) continue;
    seen.add(it.job_id);
    out.push(it);
  }
  return out;
}

/**
 * How many POSTINGS in this digest the model rated "Strong fit".
 *
 * Two things this does not do, both of which it used to do inline on the
 * dashboard and both of which inflated the figure:
 *
 * It counts distinct job_id, not items. `items` holds one row per
 * (posting, track), so a posting the build filed under both tracks was
 * counted twice -- "2 scored a strong fit" for one role. New builds write the
 * tracks disjoint, but a digest is frozen once written, so the de-duplication
 * has to live on this side too.
 *
 * And it reads `tier`, which is the rating the sentence claims, not
 * `fit_score >= 0.8`. They are different functions of the same four
 * probabilities (argmax versus a weighted sum; see services/jobs/score.py),
 * so the threshold both missed Strong fits scoring below 0.8 and counted Good
 * fits reaching it -- a line reading "N scored a strong fit" above a card
 * labelling none of them so.
 */
export function strongFitCount(items: DigestItem[]): number {
  const ids = new Set<string>();
  for (const it of items) if (it.tier === STRONG_TIER) ids.add(it.job_id);
  return ids.size;
}

export type TrackGroup = { track: DigestTrack; items: DigestItem[] };

/**
 * Items grouped by track: TRACK_ORDER first, then any other track in the order
 * first seen (a degraded digest's "unranked", or one added later). Each group
 * is in rank order, and empty groups are omitted -- including a group left
 * empty by the de-duplication below.
 *
 * A job_id appears ONCE across the whole card, under the first track that
 * claims it in the order above. The backend now writes the tracks disjoint
 * (services/jobs/digest.py), so for a digest built after that change this
 * drops nothing. It is here because a digest is frozen once written: the ones
 * already in the database hold a posting under both headings, and they would
 * otherwise keep rendering it twice -- and keep counting it twice in the
 * per-track heading counts -- until they aged out.
 */
export function groupByTrack(items: DigestItem[]): TrackGroup[] {
  const by = new Map<string, DigestItem[]>();
  for (const it of items) {
    const list = by.get(it.track);
    if (list) list.push(it);
    else by.set(it.track, [it]);
  }
  const order = [
    ...TRACK_ORDER.filter((t) => by.has(t)),
    ...[...by.keys()].filter((t) => !(TRACK_ORDER as readonly string[]).includes(t)),
  ];
  const seen = new Set<string>();
  const groups: TrackGroup[] = [];
  for (const track of order) {
    const rows: DigestItem[] = [];
    for (const it of [...by.get(track)!].sort((a, b) => a.rank - b.rank)) {
      if (seen.has(it.job_id)) continue;
      seen.add(it.job_id);
      rows.push(it);
    }
    if (rows.length > 0) groups.push({ track, items: rows });
  }
  return groups;
}
