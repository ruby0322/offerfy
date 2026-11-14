"use client";

import { useFormatter, useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import type { Digest } from "@/lib/digest";
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

  const picks = (digest?.items ?? [])
    .slice()
    .sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0))
    .slice(0, PICKS);
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
                <li
                  key={item.job_id}
                  className="flex items-center gap-2.5 border-b border-rule py-2 last:border-b-0"
                >
                  <EmployerLogo company={item.company} size="sm" />
                  <span className="min-w-0 flex-1">
                    <Link
                      href={`/jobs/${encodeURIComponent(item.job_id)}`}
                      className="block truncate text-[0.8125rem] font-medium"
                    >
                      {item.job_title}
                    </Link>
                    <span className="block truncate text-xs text-muted-foreground">
                      {item.company}
                    </span>
                  </span>
                  {/* Fixed width, so the scores form a column you can read
                      down however wide the button beside them is. */}
                  <span
                    className={`w-8 shrink-0 text-right font-display text-[1.05rem] font-medium [font-variant-numeric:tabular-nums] ${
                      (item.fit_score ?? 0) >= 0.8 ? "text-success" : ""
                    }`}
                  >
                    {item.fit_score === null ? "—" : Math.round(item.fit_score * 100)}
                  </span>
                  <span className="w-[4.75rem] shrink-0 text-right">
                    <Link
                      href={`/jobs/${encodeURIComponent(item.job_id)}`}
                      className="rr-btn px-2.5 py-1 text-xs"
                    >
                      {t("digestTailor")}
                    </Link>
                  </span>
                </li>
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
                <li
                  key={job.id}
                  className="flex items-center gap-2.5 border-b border-rule py-2 last:border-b-0"
                >
                  <EmployerLogo company={job.company} size="sm" />
                  <span className="min-w-0 flex-1">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="block truncate text-[0.8125rem] font-medium"
                    >
                      {job.title}
                    </Link>
                    <span className="block truncate text-xs text-muted-foreground">
                      {job.company}
                      {job.location ? ` · ${job.location}` : ""}
                    </span>
                  </span>
                  {/* An em dash in the same slot as a score, rather than the
                      words "not scored" on every row: the column header says
                      it once, and the rows stay aligned. */}
                  <span
                    className="w-8 shrink-0 text-right font-display text-[1.05rem] font-medium text-muted-foreground"
                    title={t("freshNotScored")}
                  >
                    —
                  </span>
                  <span className="w-[4.75rem] shrink-0 text-right">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="rr-btn rr-btn-ghost px-2.5 py-1 text-xs"
                    >
                      {t("open")}
                    </Link>
                  </span>
                </li>
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
