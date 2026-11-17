"use client";

import { useFormatter, useLocale, useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import RoleDeck, { type DeckCard } from "./RoleDeck";
import type { AppLocale } from "@/i18n/routing";
import { groupByDay, sourceLabel, type JobListItem } from "@/lib/jobs";

/**
 * The morning's ingest, one posting to a page.
 *
 * The mirror image of a weekly pick, and the difference is the point. Nothing
 * has read these postings, so there is no score, no sentence and no gap --
 * and there is NO SCORE SLOT either, not an empty one. A blank where a number
 * goes is a promise that the number is on its way, and whether any of these
 * is ever scored depends on an ordering and a cap that this card knows
 * nothing about. MatchRow carries the same scar in its own docstring.
 *
 * What these have that a pick does not: a location, whether it is remote, and
 * which board it came from. They arrive as JobListItem rather than DigestItem,
 * so all three are already on the page and cost nothing to show.
 *
 * The day is printed on each card, where the list prints it as a group
 * heading. It is NOT repeated as a "posted" fact underneath: the same date
 * twice, a line apart, reads as two different dates that happen to match.
 */

type Props = {
  /** What the dashboard returned: the newest few, already capped by the API. */
  items: JobListItem[];
  /**
   * How many postings match the preference in all. The API returns at most
   * MATCH_PREVIEW of them, and until now nothing said so -- a deck that stops
   * at twelve looks like a preference that matches twelve.
   */
  totalCount: number;
};

export default function FreshDeck({ items, totalCount }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const format = useFormatter();
  const locale = useLocale() as AppLocale;

  if (items.length === 0) return null;

  // The same grouping the list renders, flattened: day order, then API order
  // inside a day, so switching view keeps your place.
  const cards: DeckCard[] = groupByDay(items).flatMap(({ day, items: rows }) => {
    const label =
      day === "unknown"
        ? t("freshUndated")
        : format.dateTime(new Date(day), {
            weekday: "long",
            month: "short",
            day: "numeric",
          });
    return rows.map((job) => {
      const where = [job.location, job.remote ? tJobs("remote") : null]
        .filter(Boolean)
        .join(" · ");
      return {
        key: job.id,
        node: (
          <article className="flex min-w-0 flex-1 flex-col rounded-lg border border-rule bg-sheet p-4">
            <p className="text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
              {label}
            </p>
            <div className="mt-1.5 flex min-w-0 items-start gap-2.5">
              <span className="shrink-0">
                <EmployerLogo company={job.company} />
              </span>
              <span className="min-w-0">
                <Link
                  href={`/jobs/${encodeURIComponent(job.id)}`}
                  className="block break-words text-[0.95rem] font-semibold leading-snug"
                >
                  {job.title}
                </Link>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  {job.company}
                </span>
              </span>
            </div>

            <dl className="mt-3 border-t border-rule pt-2.5 text-xs text-muted-foreground">
              {where ? (
                <div className="flex items-baseline justify-between gap-3 py-0.5">
                  <dt>{tJobs("whereLabel")}</dt>
                  <dd className="min-w-0 break-words text-right font-medium text-foreground">
                    {where}
                  </dd>
                </div>
              ) : null}
              <div className="flex items-baseline justify-between gap-3 py-0.5">
                <dt>{t("colSource")}</dt>
                <dd className="font-medium text-foreground">
                  {sourceLabel(job.source, locale)}
                </dd>
              </div>
            </dl>

            {/* The only action, so it takes the card's primary weight. Applying
                and tailoring both stay on the posting's own page: an apply
                records markApplied(jobId), which is where the whole tracked
                pipeline comes from, and a bare apply_url here would send
                someone out without it. */}
            <div className="mt-auto flex gap-2 pt-3.5">
              <Link
                href={`/jobs/${encodeURIComponent(job.id)}`}
                className="rr-btn rr-btn-accent rr-btn-sm flex-1"
              >
                {t("deckOpenJob")}
              </Link>
            </div>
          </article>
        ),
      };
    });
  });

  const end = (
    <article className="flex min-w-0 flex-1 flex-col items-center justify-center rounded-lg border border-rule bg-sheet p-6 text-center">
      <p className="text-[0.95rem] font-semibold">
        {t("freshDoneHeading", { count: items.length })}
      </p>
      {totalCount > items.length ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t("freshDoneBody", { count: totalCount })}
        </p>
      ) : null}
      <div className="mt-4 flex w-full flex-col gap-2">
        <Link href="/jobs" className="rr-btn rr-btn-ghost rr-btn-sm">
          {t("browseRoles")}
        </Link>
      </div>
    </article>
  );

  return (
    <RoleDeck cards={cards} end={end} label={t("freshHeading")} testId="matches-fresh-deck" />
  );
}
