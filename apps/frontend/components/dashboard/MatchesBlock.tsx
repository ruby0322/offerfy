"use client";

import { useFormatter, useTranslations } from "next-intl";
import MatchRow from "./MatchRow";
import { Link } from "@/i18n/navigation";
import { uniqueByJob, type Digest } from "@/lib/digest";
import type { JobListItem } from "@/lib/jobs";

/**
 * Matches on the overview: loud, and three lines a role.
 *
 * It gets the only display-serif heading on the page and sits directly under
 * the next action, because fresh roles are the reason to open the dashboard
 * on a day when nothing else has changed.
 *
 * Concise comes from cutting the rows, not the block: the reason and gap
 * paragraphs belong on the Matches tab, so here a role is a mark, a title, an
 * employer, a score and one button. Two columns keep the weekly scoring and
 * the daily ingest visibly apart -- a score on the left, "not scored" on the
 * right -- which is the distinction the old build lost by treating the match
 * count as a *fallback* for the digest, so the two were never on screen
 * together.
 *
 * The employer mark leads every row and does more work than decoration:
 * eight Taipei engineering titles are near-identical as text, and the mark is
 * what you actually scan by.
 */

type Props = {
  digest: Digest | null;
  matches: JobListItem[];
  matchCount: number;
  /** Null when we had no last-visit marker to ask with. */
  newMatchCount: number | null;
};

const PICKS = 3;
const FRESH = 3;

export default function MatchesBlock({
  digest,
  matches,
  matchCount,
  newMatchCount,
}: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();

  // One row per posting before the slice: `items` is one row per
  // (posting, track), so a posting in both tracks used to take two of these
  // three slots -- the same role listed twice, under duplicate React keys.
  const picks = uniqueByJob(
    [...(digest?.items ?? [])].sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0)),
  ).slice(0, PICKS);
  const fresh = matches.slice(0, FRESH);
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
          {newMatchCount !== null && newMatchCount > 0 ? (
            <>
              {" · "}
              <span className="font-semibold text-success [font-variant-numeric:tabular-nums]">
                {newMatchCount}
              </span>{" "}
              {t("matchesNewThisWeek")}
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
            <p className="mt-2 text-sm text-muted-foreground">{t("picksEmpty")}</p>
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
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-[0.8125rem] font-bold">{t("freshHeading")}</p>
            <p className="text-xs text-muted-foreground">{t("freshNotScored")}</p>
          </div>
          {fresh.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">{t("matchEmpty")}</p>
          ) : (
            <ul className="mt-1">
              {fresh.map((job) => (
                <MatchRow
                  key={job.id}
                  jobId={job.id}
                  title={job.title}
                  company={job.company}
                  meta={job.location}
                  score={null}
                  unscoredTitle={t("freshNotScored")}
                  action={
                    <Link
                      href={`/jobs/${job.id}`}
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
