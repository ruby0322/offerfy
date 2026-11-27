"use client";

import { useFormatter, useTranslations } from "next-intl";
import MatchRow from "./MatchRow";
import SamplePick from "./SamplePick";
import { Link } from "@/i18n/navigation";
import { arrivalState } from "./arrivals";
import type { Readiness } from "./onboarding";
import { uniqueByJob, type Digest } from "@/lib/digest";
import type { MatchArrival } from "@/lib/dashboard";
import type { JobListItem } from "@/lib/jobs";

/**
 * Matches on the overview: one card, two columns.
 *
 * This is the original layout, restored on request after the arrivals work
 * briefly split it into a separate card plus two stacked sections. One card
 * reads better: the display-serif heading frames the whole thing, and the two
 * columns put the week's judgement beside the week's new roles instead of
 * making the reader scroll from one to the other.
 *
 * Left is the weekly digest -- picked, scored and explained -- which is the
 * frozen record kept on screen rather than merely stored, so the prose it buys
 * is read by someone.
 *
 * Right is what is new to the reader, from `arrivals`. It falls back to the
 * standing match set when nothing has arrived, and the heading says which of
 * the two it is rather than claiming novelty for a list that has none. The
 * fallback is what stops the column going blank for someone who opens the
 * dashboard twice in an hour, which is the one thing the two-column layout
 * must not do.
 *
 * Concise comes from cutting the rows, not the block: the reason and gap
 * paragraphs belong on the Matches tab, so here a role is a mark, a title, an
 * employer, a score and one button.
 *
 * The right column shows its score, which is a change from the layout this
 * restores. Back then the two columns meant "scored on the left, unscored on
 * the right", because the right one was the daily ingest and nothing had read
 * it. The sweep scores everything now, so that distinction is gone and hiding
 * a number we already hold would be throwing away a real signal. What the
 * columns separate instead is why a role is here at all: picked for you this
 * week, against new to you since you looked.
 *
 * A row the sweep has not reached shows no number -- inventing one would be
 * worse than a gap -- but it does carry a "scoring" tag in the slot under the
 * number, which keeps the column from going ragged and says a number really is
 * coming. A REFUSED row gets neither: a score for it is never coming, so the
 * tag would be a promise. `arrivalState` is what tells those two apart. The
 * standing-match fallback gets no tag either, because its blank means "no
 * score field in this list" rather than "being scored".
 *
 * A posting that closed after it was listed is labelled rather than dropped,
 * the way DigestCard does it: sending someone to a dead posting wastes the one
 * click this whole card exists to earn.
 */

type Props = {
  digest: Digest | null;
  /** What is new to this reader since they last looked. */
  arrivals: MatchArrival[];
  /** The current match set, newest first (dash.matches). */
  matches: JobListItem[];
  matchCount: number;
  /**
   * What still has to happen before this person can have anything here, in
   * order. It replaces a `hasPreference` boolean, which could only name the
   * second of the two gates: matching reads a saved resume, so someone
   * without one was being asked what they want next while the answer could
   * not change anything.
   */
  readiness: Readiness;
};

const PICKS = 3;
const FRESH = 3;

/** The two sources the right column can draw on, in one shape. */
type Role = {
  id: string;
  title: string;
  company: string;
  location: string | null;
  /** 0..1, or null for a posting the sweep has not scored yet. */
  score: number | null;
  /**
   * True only for an arrival still waiting on the sweep -- it gets the
   * "scoring" tag, which fills the gap a missing number leaves and tells the
   * reader a number is genuinely coming.
   *
   * `arrivalState` decides it, and the reason to go through that function
   * rather than testing `fit_score === null` is the third state it knows
   * about: a row whose `scored_at` is set but which carries no score is a
   * REFUSAL, and a score for it is never coming. Tagging that "scoring"
   * promises something that will not arrive.
   *
   * False for the standing fallback even though those rows also show no
   * number: `JobListItem` carries no score field, so its blank means "not
   * available in this list", not "being scored". Many of those postings have
   * been scored already. Tagging them would claim work that is not happening.
   */
  pending: boolean;
  /** The posting closed after it was listed; the row has to say so. */
  inactive: boolean;
};

export default function MatchesBlock({
  digest,
  arrivals,
  matches,
  matchCount,
  readiness,
}: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();

  // One sentence for both columns, because both are empty for the same reason
  // and two different accounts of it would read as two different problems.
  // `null` once nothing is outstanding: the columns then say what is true of
  // their own contents instead.
  const blocked =
    readiness === "no_resume"
      ? t("matchNeedsResume")
      : readiness === "no_preference"
        ? t("matchEmpty")
        : null;

  // One row per posting before the slice: `items` is one row per
  // (posting, track), so a posting in both tracks used to take two of these
  // three slots -- the same role listed twice, under duplicate React keys.
  const picks = uniqueByJob(
    [...(digest?.items ?? [])].sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0)),
  ).slice(0, PICKS);

  // Arrivals lead; the standing set is the fallback. `isNew` drives the
  // heading, so the column never calls a standing list "new".
  const isNew = arrivals.length > 0;
  const fresh: Role[] = (
    isNew
      ? arrivals.map((a) => ({
          id: a.job_id,
          title: a.job_title,
          company: a.company,
          location: a.location,
          score: a.fit_score,
          pending: arrivalState(a) === "pending",
          inactive: !a.job_is_active,
        }))
      : matches.map((j) => ({
          id: j.id,
          title: j.title,
          company: j.company,
          location: j.location ?? null,
          // The standing set is a posting list, not a judgement: it has no
          // score to carry, so this column is uniformly unscored in fallback.
          score: null,
          pending: false,
          inactive: !j.is_active,
        }))
  ).slice(0, FRESH);

  const built = digest ? new Date(digest.built_at) : null;
  const builtValid = built !== null && !Number.isNaN(built.getTime());

  return (
    <section
      className="rr-card px-5 py-4"
      data-testid="dashboard-matches"
      aria-labelledby="dash-matches"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="dash-matches"
          className="font-display text-[1.15rem] font-medium tracking-[-0.025em]"
        >
          {t("headingMatches")}
        </h2>
        <p className="text-sm text-muted-foreground">
          <span className="text-[0.9rem] font-semibold text-foreground [font-variant-numeric:tabular-nums]">
            {matchCount}
          </span>{" "}
          {t("matchesFitFilters")}
          {arrivals.length > 0 ? (
            <>
              {" · "}
              <span className="font-semibold text-success [font-variant-numeric:tabular-nums]">
                {arrivals.length}
              </span>{" "}
              {t("matchesNewCount")}
            </>
          ) : null}
        </p>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-x-5 md:grid-cols-2">
        <div className="min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-[0.8125rem] font-bold">{t("picksHeading")}</p>
            {builtValid ? (
              <p className="text-xs text-muted-foreground">
                {t("picksScoredOn", {
                  date: format.dateTime(built, { month: "short", day: "numeric" }),
                })}
              </p>
            ) : null}
          </div>
          {picks.length === 0 ? (
            // No digest has ever been built: this account has not had a week
            // yet, so show the shape of one. A real quiet week (a digest
            // exists, with no items) keeps the plain sentence -- an example
            // there would read as a pick that went missing.
            //
            // The sentence under it is the part that was missing. The example
            // says what a pick looks like and nothing about when this reader
            // gets one, so the column read as broken to exactly the person it
            // was built to reassure. `picksNoneYet` already said the right
            // thing on the Matches tab, which is not the screen they land on.
            digest === null ? (
              <>
                <SamplePick />
                <p className="mt-2 text-sm text-muted-foreground">
                  {blocked ?? t("picksNoneYet")}
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">{t("picksEmpty")}</p>
            )
          ) : (
            <ul className="mt-1">
              {picks.map((item) => (
                <MatchRow
                  key={item.job_id}
                  jobId={item.job_id}
                  title={item.job_title}
                  company={item.company}
                  score={item.fit_score}
                  action={
                    <Link
                      href={`/jobs/${encodeURIComponent(item.job_id)}`}
                      className="rr-btn rr-btn-accent rr-btn-sm"
                    >
                      {t("digestTailor")}
                    </Link>
                  }
                />
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 min-w-0 border-t border-rule pt-3 md:mt-0 md:border-l md:border-t-0 md:pl-5 md:pt-0">
          <p className="text-[0.8125rem] font-bold">
            {isNew ? t("freshHeading") : t("standingHeading")}
          </p>
          {fresh.length === 0 ? (
            // Someone who has already done both steps is not asked to do them
            // again: with a readable resume and a saved preference an empty
            // list means nothing matches, not that they have told us nothing.
            <p className="mt-2 text-sm text-muted-foreground">
              {blocked ?? t("matchNone")}
            </p>
          ) : (
            <ul className="mt-1">
              {fresh.map((role) => (
                <MatchRow
                  key={role.id}
                  jobId={role.id}
                  title={role.title}
                  company={role.company}
                  meta={role.location}
                  score={role.score}
                  tier={role.pending ? t("matchesScoringTag") : null}
                  inactiveLabel={role.inactive ? t("jobInactive") : null}
                  action={
                    <Link
                      href={`/jobs/${encodeURIComponent(role.id)}`}
                      className="rr-btn rr-btn-ghost rr-btn-sm"
                    >
                      {t("open")}
                    </Link>
                  }
                />
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <Link href="/dashboard/matches" className="underline underline-offset-2">
          {t("matchesSeeAll")}
        </Link>
        <Link href="/jobs" className="underline underline-offset-2">
          {t("browseRoles")}
        </Link>
      </div>
    </section>
  );
}
