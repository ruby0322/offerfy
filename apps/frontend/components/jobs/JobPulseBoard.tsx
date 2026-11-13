import type React from "react";
import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { JobTime, RefreshedAt } from "@/components/jobs/Timestamps";
import type { JobPulse } from "@/lib/jobs";

/** The one tag the pulse strip's messages use. */
const strong = (chunks: React.ReactNode) => <b>{chunks}</b>;

/**
 * The collapsed card's whole content, so the same employer twice is a wasted
 * slot: "just posted" is often three roles at one company.
 */
const MARK_CAP = 8;
const marksOf = (companies: string[]) => Array.from(new Set(companies)).slice(0, MARK_CAP);

type Props = {
  pulse: JobPulse;
  /** Where "see all new" goes — a board page keeps its own scope. */
  newHref?: string;
  /**
   * False on a board page, which shows the strip and then its own accent
   * block (the employers hiring interns, the cities roles are in) instead of
   * the catalogue-wide pair.
   */
  boards?: boolean;
};

/**
 * The live board: how big the catalogue is, what just landed, who is hiring
 * hardest. One server fetch (/v1/jobs/pulse) feeds all three, so the strip
 * and the two lists can never disagree with each other.
 *
 * It renders nothing at all when the catalogue is empty rather than a row of
 * zeroes — an empty catalogue means ingest is broken, and "0 open roles ·
 * +0 in the last 24h" is a worse way to say that than the empty state below
 * it already does.
 *
 * The two lists are `<details>`, closed by default: between them they own
 * most of the first screen, and someone who came to search wants them out of
 * the way. Closed, a card is its heading and its employers' marks — the
 * glanceable half of a row, and the half that says whether the list is worth
 * opening — so the titles are traded for a strip of logos rather than for a
 * bare heading. Native disclosure rather than React state on purpose: this is a
 * server component, the summary is a real button to a screen reader without
 * being told so, and the whole thing works before (and without) JavaScript.
 * "See all new" sits in a foot row rather than the summary, because a link
 * inside a `<summary>` toggles the card on its way out.
 */
export default async function JobPulseBoard({
  pulse,
  newHref = "/jobs?postedWithinDays=1",
  boards = true,
}: Props) {
  const t = await getTranslations("jobs");
  if (pulse.active_total === 0) return null;

  const mark = (company: string) => <EmployerLogo key={company} company={company} size="sm" />;

  return (
    <section className="jobs-pulse" aria-label={t("live")}>
      <div className="jobs-pulse-strip">
        <span className="jobs-pulse-live">
          <span className="jobs-pulse-dot" aria-hidden="true" />
          {t("live")}
        </span>
        {/* Rich rather than string concatenation: only the number is set in
            ink, and which side of the number the words go on differs between
            English and Chinese. */}
        <span>{t.rich("openRoles", { count: pulse.active_total, b: strong })}</span>
        {pulse.new_24h > 0 ? (
          <span className="jobs-pulse-new">
            {t.rich("newInDay", { count: pulse.new_24h, b: strong })}
          </span>
        ) : null}
        <span>{t.rich("employerCount", { count: pulse.employer_count, b: strong })}</span>
        <RefreshedAt iso={pulse.refreshed_at} />
      </div>

      {boards ? (
      <div className="jobs-pulse-boards">
        {pulse.newest.length > 0 ? (
          <details className="jobs-pulse-card">
            <summary className="jobs-pulse-head">
              <h2>{t("justPosted")}</h2>
              <span className="jobs-pulse-marks">
                {marksOf(pulse.newest.map((job) => job.company)).map(mark)}
              </span>
              <span className="jobs-pulse-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            {pulse.newest.map((job) => (
              <Link key={job.id} href={`/jobs/${job.id}`} className="jobs-pulse-row">
                <EmployerLogo company={job.company} size="sm" />
                <span className="jobs-pulse-grow">
                  <span className="jobs-pulse-title">{job.title}</span>
                  <span className="jobs-pulse-meta">
                    {job.company}
                    {job.location ? ` · ${job.location}` : null}
                  </span>
                </span>
                {job.posted_at ? <JobTime iso={job.posted_at} /> : null}
              </Link>
            ))}
            <div className="jobs-pulse-foot">
              <Link href={newHref}>{t("seeAllNew")}</Link>
            </div>
          </details>
        ) : null}

        {pulse.top_employers.length > 0 ? (
          <details className="jobs-pulse-card">
            <summary className="jobs-pulse-head">
              <h2>{t("hiringHardest")}</h2>
              <span className="jobs-pulse-marks">
                {marksOf(pulse.top_employers.map((employer) => employer.company)).map(mark)}
              </span>
              <span className="jobs-pulse-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            {pulse.top_employers.map((employer) => (
              <Link
                key={employer.company}
                href={`/jobs?company=${encodeURIComponent(employer.company)}`}
                className="jobs-pulse-row"
              >
                <EmployerLogo company={employer.company} size="sm" />
                <span className="jobs-pulse-grow">
                  <span className="jobs-pulse-title">{employer.company}</span>
                  <span className="jobs-pulse-meta">
                    {t("employerOpenCount", { count: employer.open_count })}
                  </span>
                </span>
                {employer.new_count > 0 ? (
                  <span className="jobs-pulse-delta">
                    {t("employerNewCount", { count: employer.new_count })}
                  </span>
                ) : null}
              </Link>
            ))}
          </details>
        ) : null}
      </div>
      ) : null}
    </section>
  );
}
