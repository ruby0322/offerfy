"use client";

import DigestCard from "@/components/dashboard/DigestCard";
import MatchCountCard from "@/components/dashboard/MatchCountCard";
import type { Digest } from "@/lib/digest";
import type { JobListItem } from "@/lib/jobs";

type Props = {
  digest: Digest | null;
  matchCount: number;
  // Their match rows ride into the fallback: the count card is what a thin
  // week shows, and it now shows the first few roles rather than a bare number.
  matches: JobListItem[];
  hasPreference: boolean;
};

/**
 * The dashboard's second slot: this week's digest, else the match count. A
 * null digest is the only case decided here; whether a digest has anything in
 * it is DigestCard's call, and it hands back the fallback when it does not.
 */
export default function SlotTwo({ digest, matchCount, matches, hasPreference }: Props) {
  const match = (
    <MatchCountCard count={matchCount} hasPreference={hasPreference} matches={matches} />
  );
  return digest ? <DigestCard digest={digest} fallback={match} /> : match;
}
