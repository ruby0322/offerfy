"use client";

import DigestCard from "@/components/dashboard/DigestCard";
import MatchCountCard from "@/components/dashboard/MatchCountCard";
import type { Digest } from "@/lib/digest";

type Props = {
  digest: Digest | null;
  matchCount: number;
  hasPreference: boolean;
};

/**
 * The dashboard's second slot: this week's digest, else the match count. A
 * null digest is the only case decided here; whether a digest has anything in
 * it is DigestCard's call, and it hands back the fallback when it does not.
 */
export default function SlotTwo({ digest, matchCount, hasPreference }: Props) {
  const match = <MatchCountCard count={matchCount} hasPreference={hasPreference} />;
  return digest ? <DigestCard digest={digest} fallback={match} /> : match;
}
