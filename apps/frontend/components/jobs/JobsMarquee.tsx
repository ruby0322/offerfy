import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import type { AppLocale } from "@/i18n/routing";
import { sourceLabel, type JobListItem } from "@/lib/jobs";

type Props = {
  locale: AppLocale;
  jobs: JobListItem[];
  label?: string;
};

export default async function JobsMarquee({ locale, jobs, label }: Props) {
  const t = await getTranslations("jobs");
  if (jobs.length === 0) {
    return null;
  }
  const heading = label ?? t("featuredLabel");
  const loop = jobs.length > 1 ? [...jobs, ...jobs] : jobs;
  return (
    <section className="jobs-marquee" aria-label={heading}>
      <h2 className="jobs-featured-label">{heading}</h2>
      <div className="jobs-marquee-viewport">
        <div className={`jobs-marquee-track${jobs.length > 1 ? " jobs-marquee-loop" : ""}`}>
          {loop.map((job, index) => {
            const dup = index >= jobs.length;
            return (
              <Link
                key={`${job.id}-${index}`}
                href={`/jobs/${job.id}`}
                className={`jobs-card${dup ? " jobs-card-dup" : ""}`}
                tabIndex={dup ? -1 : undefined}
                aria-hidden={dup || undefined}
              >
                <p className="blog-meta">
                  <span>{sourceLabel(job.source, locale)}</span>
                  <span aria-hidden="true"> · </span>
                  <span>{job.company}</span>
                </p>
                <h3 className="font-display">{job.title}</h3>
                <p className="jobs-card-place">
                  {job.location ? job.location : null}
                  {job.location && job.remote ? " · " : null}
                  {job.remote ? t("remote") : null}
                </p>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}
