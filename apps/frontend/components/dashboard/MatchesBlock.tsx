"use client";

import { useFormatter, useTranslations } from "next-intl";
import MatchRow from "./MatchRow";
import SamplePick from "./SamplePick";
import { Link } from "@/i18n/navigation";
import { uniqueByJob, type Digest } from "@/lib/digest";
import type { JobListItem } from "@/lib/jobs";

/**
 * The weekly picks on the overview, below the arrivals.
 *
 * Two surfaces, two jobs. ArrivalsBlock above answers "what landed since you
 * looked"; this answers "this week, picked and explained" -- the frozen
 * digest, kept on screen rather than merely stored, so the prose it buys is
 * read by someone. A posting can appear in both. That overlap is accepted.
 *
 * This used to be two columns, scored picks on the left and the daily ingest
 * unscored on the right. The columns drew a distinction the sweep has erased
 * -- everything is scored now, just not instantly. The picks stay; the right
 * column's list returns below them as "currently matching", a different
 * question from the arrivals block's "new since you looked".
 *
 * Concise comes from cutting the rows, not the block: the reason and gap
 * paragraphs belong on the Matches tab, so here a role is a mark, a title, an
 * employer, a score and one button.
 */

type Props = {
  digest: Digest | null;
  /** The current match set, newest first (dash.matches). */
  matches: JobListItem[];
  matchCount: number;
};

const PICKS = 3;
const STANDING = 3;

export default function MatchesBlock({ digest, matches, matchCount }: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();

  // One row per posting before the slice: `items` is one row per
  // (posting, track), so a posting in both tracks used to take two of these
  // three slots -- the same role listed twice, under duplicate React keys.
  const picks = uniqueByJob(
    [...(digest?.items ?? [])].sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0)),
  ).slice(0, PICKS);
  const standing = matches.slice(0, STANDING);
  const built = digest ? new Date(digest.built_at) : null;
  const builtValid = built !== null && !Number.isNaN(built.getTime());

  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-matches" aria-labelledby="dash-matches">
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
        </p>
      </div>

      <div className="mt-3 min-w-0">
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
          digest === null ? (
            <SamplePick />
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

      {/* Standing matches: what fits you now, newest first, claiming nothing
          about recency. A posting is new to you once, in the arrivals block;
          this list is what is left on a second visit an hour later, so the
          overview never has no roles at all while match_count > 0. */}
      <div className="mt-4 min-w-0 border-t border-rule pt-3">
        <p className="text-[0.8125rem] font-bold">{t("standingHeading")}</p>
        {standing.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">{t("matchEmpty")}</p>
        ) : (
          <ul className="mt-1">
            {standing.map((job) => (
              <MatchRow
                key={job.id}
                jobId={job.id}
                title={job.title}
                company={job.company}
                meta={job.location}
                score={null}
                action={
                  <Link href={`/jobs/${job.id}`} className="rr-btn rr-btn-ghost rr-btn-sm">
                    {t("open")}
                  </Link>
                }
              />
            ))}
          </ul>
        )}
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
