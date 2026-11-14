import type { Application } from "@/lib/applications";
import type { Digest } from "@/lib/digest";
import type { ResumeHealth } from "@/lib/dashboard";
import { QUIET_DAYS, quietDays, readyAgeDays } from "./applications";

/**
 * What the dashboard tells you to do, in one ordered list.
 *
 * The overview's whole job is to end in one finished thing, so something has
 * to decide which of eleven possible actions is first. Today every row owns
 * its own button and nothing ranks them; this is that ranking, in one place,
 * so the band at the top and the table's sort cannot disagree about which
 * application matters most (see `urgencyRank`, which follows the same order).
 *
 * The ladder, and why it is in this order:
 *
 * 1. Anything blocking matching entirely — an unpublished resume, no stated
 *    preference, a stale reading. These are not tasks competing with the
 *    others; while one is true the rest of the dashboard is partly fiction.
 * 2. A tailored resume never sent. Work already paid for and not yet spent,
 *    and it decays: a posting closes while the resume sits.
 * 3. An application gone quiet past the two-week line. Only you can chase it.
 * 4. An outbound click never confirmed. One tap, but bookkeeping rather than
 *    progress, so it loses to anything that moves the search forward.
 * 5. Tailoring for the week's strongest fit. Genuinely valuable, but it is
 *    new work, and new work waits until the work in flight is unblocked.
 */

export type NextStepKind =
  | "publish"
  | "preferences"
  | "rebuild"
  | "send"
  | "followUp"
  | "confirm"
  | "tailor";

export type NextStep = {
  kind: NextStepKind;
  /** i18n keys in the `dashboard` namespace. */
  titleKey: string;
  whyKey: string;
  /** Values the two keys interpolate. */
  params: Record<string, string | number>;
  /** Where the primary button goes; null when the caller supplies the action. */
  href: string | null;
  /** The application or posting this is about, when it is about one. */
  jobId?: string;
};

export type NextStepInput = {
  applications: Application[];
  health: ResumeHealth;
  digest: Digest | null;
  /** False until the person has said what they want next. */
  hasPreference: boolean;
  masterId: string | null;
};

/** Oldest first, so the most neglected row leads its own group. */
function byAgeDesc(rows: Application[]): Application[] {
  return [...rows].sort(
    (a, b) => Date.parse(a.last_status_at) - Date.parse(b.last_status_at),
  );
}

export function nextSteps(input: NextStepInput, now: Date = new Date()): NextStep[] {
  const { applications, health, digest, hasPreference, masterId } = input;
  const steps: NextStep[] = [];

  // 1. Blockers. A resume nothing can read makes every number below it a
  //    guess, so these come before any amount of pending work.
  if (health.has_master && !health.published) {
    steps.push({
      kind: "publish",
      titleKey: "nextPublish",
      whyKey: "nextPublishWhy",
      params: {},
      href: masterId ? `/editor/${masterId}` : "/new",
    });
  }
  if (!hasPreference) {
    steps.push({
      kind: "preferences",
      titleKey: "nextPreferences",
      whyKey: "nextPreferencesWhy",
      params: {},
      href: "/preferences",
    });
  }
  if (health.published && health.needs_rebuild) {
    steps.push({
      kind: "rebuild",
      titleKey: "nextRebuild",
      whyKey: "nextRebuildWhy",
      params: {},
      // The rebuild spends credits, so it only ever runs from a button the
      // person presses; the caller wires that, not a link.
      href: null,
    });
  }

  // 2. Tailored and never sent.
  for (const app of byAgeDesc(applications.filter((a) => a.status === "tailored"))) {
    steps.push({
      kind: "send",
      titleKey: "nextSendTailored",
      whyKey: "nextSendTailoredWhy",
      params: {
        company: app.company,
        title: app.job_title,
        days: readyAgeDays(app, now) ?? 0,
      },
      href: null,
      jobId: app.job_id,
    });
  }

  // 3. Applied and gone quiet.
  for (const app of byAgeDesc(
    applications.filter((a) => a.status === "applied" && quietDays(a, now) > QUIET_DAYS),
  )) {
    steps.push({
      kind: "followUp",
      titleKey: "nextFollowUp",
      whyKey: "nextFollowUpWhy",
      params: {
        company: app.company,
        title: app.job_title,
        days: quietDays(app, now),
      },
      href: null,
      jobId: app.job_id,
    });
  }

  // 4. Applied but never confirmed.
  for (const app of byAgeDesc(
    applications.filter(
      (a) =>
        a.status === "applied" &&
        a.applied_confirmed_at === null &&
        quietDays(a, now) <= QUIET_DAYS,
    ),
  )) {
    steps.push({
      kind: "confirm",
      titleKey: "nextConfirm",
      whyKey: "nextConfirmWhy",
      params: {
        company: app.company,
        title: app.job_title,
        days: quietDays(app, now),
      },
      href: null,
      jobId: app.job_id,
    });
  }

  // 5. The week's strongest fit, if it is not already in the pipeline.
  if (digest) {
    const tracked = new Set(applications.map((a) => a.job_id));
    const best = [...digest.items]
      .filter((item) => item.job_is_active && !tracked.has(item.job_id))
      .sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0))[0];
    if (best) {
      steps.push({
        kind: "tailor",
        titleKey: "nextTailor",
        whyKey: "nextTailorWhy",
        params: {
          company: best.company,
          title: best.job_title,
          score:
            best.fit_score === null ? "—" : `${Math.round(best.fit_score * 100)}`,
        },
        href: `/jobs/${encodeURIComponent(best.job_id)}`,
        jobId: best.job_id,
      });
    }
  }

  return steps;
}
