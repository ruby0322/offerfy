"use client";

import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import JobRow from "@/components/jobs/JobRow";
import type { AppLocale } from "@/i18n/routing";
import type { JobListItem } from "@/lib/jobs";

type Props = {
  count: number;
  /** False until the person says what they want; then there is no count to show. */
  hasPreference: boolean;
  /** The first few of those matches, newest first. Empty when the count is 0. */
  matches: JobListItem[];
};

/**
 * The matches, as roles rather than as a number.
 *
 * The rows are the jobs page's own rows (components/jobs/JobRow), not a
 * dashboard-shaped summary of them: a match is a posting, it opens the same
 * page, and someone who has seen one list of roles on this site should
 * recognise this one. The count and "browse all" stay, because the card
 * holds five and the catalogue holds the rest.
 */
export default function MatchCountCard({ count, hasPreference, matches }: Props) {
  const t = useTranslations("dashboard");
  const locale = useLocale() as AppLocale;
  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-matches" aria-labelledby="dash-matches">
      <h2 id="dash-matches" className="text-sm font-semibold tracking-tight">
        {t("headingMatches")}
      </h2>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm">
        <p>
          {hasPreference ? (
            <span className="font-medium">{t("matchCount", { count })}</span>
          ) : (
            <span className="text-muted-foreground">{t("matchEmpty")}</span>
          )}
        </p>
        <Link href="/jobs" className="underline">
          {t("browseRoles")}
        </Link>
      </div>
      {matches.length > 0 ? (
        <ul className="jobs-rows is-bare" data-testid="dashboard-match-rows">
          {matches.map((job) => (
            <li key={job.id}>
              <JobRow job={job} locale={locale} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
