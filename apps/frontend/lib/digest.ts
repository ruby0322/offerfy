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
 * first seen (a degraded digest's "unranked", or one added later). Empty
 * groups are omitted; a posting in two tracks appears in both; each group is
 * in rank order.
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
  return order.map((track) => ({
    track,
    items: [...by.get(track)!].sort((a, b) => a.rank - b.rank),
  }));
}
