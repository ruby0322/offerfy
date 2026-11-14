"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import type { AppLocale } from "@/i18n/routing";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { JobTime } from "@/components/jobs/Timestamps";
import { sourceLabel, type JobListItem } from "@/lib/jobs";

type Props = {
  job: JobListItem;
  locale: AppLocale;
};

/**
 * One posting, as a row in a list of postings.
 *
 * It lives here rather than inside JobResults because the dashboard shows
 * matches now, and a match is the same thing a search result is: the same
 * logo, title, employer line and age, opening the same page. Two copies of
 * this markup would have drifted the first time either side was touched.
 */
export default function JobRow({ job, locale }: Props) {
  const t = useTranslations("jobs");
  return (
    <Link href={`/jobs/${job.id}`} className="jobs-row">
      <EmployerLogo company={job.company} />
      <span className="jobs-row-grow">
        <span className="jobs-row-title font-display">{job.title}</span>
        <span className="jobs-row-meta">
          {job.company}
          {job.location ? ` · ${job.location}` : null}
          {job.remote ? ` · ${t("remote")}` : null}
          {` · ${sourceLabel(job.source, locale)}`}
        </span>
      </span>
      {/* posted_at is absent for most of Workday, whose boards publish
          "Posted Yesterday" as a phrase. The row simply carries no age
          rather than showing when we first saw it, which is a different
          fact. */}
      {job.posted_at ? <JobTime iso={job.posted_at} /> : null}
    </Link>
  );
}
