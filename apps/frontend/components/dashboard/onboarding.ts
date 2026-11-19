import type { ResumeHealth } from "@/lib/dashboard";
import type { JobPreference } from "@/lib/jobs";

/**
 * The pure parts of the onboarding card: when it shows, when to stop waiting,
 * and what a refusal says. The component only wires these to the screen.
 */

/**
 * The card is for the one blocking step: no saved preference yet. `updated_at`
 * is null exactly until the first save (the seed is computed, never stored).
 * It also needs a published resume -- without one the seed is empty and the
 * first run would be refused, so the card would be a form that cannot succeed;
 * the create / publish steps lead instead.
 */
export function cardVisible(
  pref: Pick<JobPreference, "updated_at">,
  health: Pick<ResumeHealth, "has_master" | "published">,
): boolean {
  return pref.updated_at === null && health.has_master && health.published;
}

/** Two seconds a poll, twenty polls: forty seconds against a run of a few. */
export const POLL_MS = 2000;
export const MAX_POLLS = 20;

export type PollVerdict = "wait" | "done" | "stop";

/**
 * What to do after the `polls`-th read of the progress endpoint.
 *
 * `scored < expected` means "not finished as far as we can see", never "still
 * working": the server has no running flag, so a run that died leaves it true
 * forever. Stopping is therefore this side's decision, by count. The count is
 * binary (0 until the pass commits once, then the final number), which is why
 * the screen shows an indeterminate indicator and never a fraction.
 */
export function pollVerdict(p: {
  scored: number;
  expected: number;
  polls: number;
}): PollVerdict {
  if (p.scored >= p.expected) return "done";
  return p.polls >= MAX_POLLS ? "stop" : "wait";
}

export type OnboardNotice =
  | "onboardNoResume"
  | "onboardPaused"
  | "onboardRunning"
  | "onboardGeneric"
  | "onboardRefreshFailed";

/**
 * The sentence for a first run that did not start, or null when saying
 * nothing is right (the account already has scores, so there is nothing to
 * wait for and nothing went wrong). An unknown reason is a generic failure,
 * never a silent success.
 *
 * `already_running` gets its own sentence because the generic one says
 * scoring did not start and will happen in the next regular pass, and for
 * this reason alone a run IS under way -- another tab took the claim.
 */
export function firstRunNotice(reason: string | null): OnboardNotice | null {
  switch (reason) {
    case "already_scored":
      return null;
    case "no_resume":
      return "onboardNoResume";
    case "budget_paused":
      return "onboardPaused";
    case "already_running":
      return "onboardRunning";
    default:
      return "onboardGeneric";
  }
}

export function addValue(list: string[], value: string): string[] {
  const lower = value.toLowerCase();
  return list.some((v) => v.toLowerCase() === lower) ? list : [...list, value];
}

export function removeValue(list: string[], value: string): string[] {
  const lower = value.toLowerCase();
  return list.filter((v) => v.toLowerCase() !== lower);
}

/** A save that says nothing would start a run over every posting. */
export function canSave(a: {
  functions: string[];
  locations: string[];
  seniority: string;
}): boolean {
  return a.functions.length > 0 || a.locations.length > 0 || a.seniority !== "";
}
