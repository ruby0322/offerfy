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
