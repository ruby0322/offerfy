import type { UsageRefusal } from "@/lib/api";

/**
 * Which message a refused resume-facts rebuild earns.
 *
 * Pure, and returns a message key rather than a string, so the mapping can be
 * tested without the i18n machinery and so the component stays the only place
 * that knows how to format a date.
 *
 * It is its own module because the mapping has already been wrong once. The
 * hourly cap on this purpose was 0 for every tier below `matching`, which made
 * a `burst` refusal mean "this button can never work for you" rather than
 * "not so fast" -- so the copy told people to switch on employer
 * introductions, which had nothing to do with it and was not something they
 * needed. Every tier that can reach the endpoint now has a real cap
 * (services/usage.py TIERS), so the tier no longer changes what a `burst`
 * means, and nothing here reads it.
 */
export type RebuildRefusalMessage = {
  key: "rebuildTooOften" | "rebuildOutOfAllowance";
  /** ISO timestamp the allowance returns; only on the allowance message. */
  resetAt?: string;
};

export function rebuildRefusal(refusal: UsageRefusal): RebuildRefusalMessage {
  if (refusal.limit === "weekly_turns" || refusal.limit === "weekly_credits") {
    return { key: "rebuildOutOfAllowance", resetAt: refusal.reset_at };
  }
  // `burst` and, as the fallback, the two instance-wide ceilings. Neither of
  // those is this button's business -- a guest cannot reach the endpoint --
  // and "try again shortly" is the vaguest true thing rather than a guess at
  // a cause.
  return { key: "rebuildTooOften" };
}
