import { getTranslations } from "next-intl/server";
import type { AppLocale } from "@/i18n/routing";
import { sourceLabel, type JobDetail } from "@/lib/jobs";

type Props = {
  locale: AppLocale;
  job: JobDetail;
};

export default async function JobView({ locale, job }: Props) {
  const t = await getTranslations("jobs");
  return (
    <article className="jobs-article mx-auto max-w-[72rem] px-5">
      <div className="blog-kicker" aria-hidden="true" />
      {!job.is_active ? <p className="jobs-closed">{t("closed")}</p> : null}
      <p className="blog-meta">
        <span>{sourceLabel(job.source, locale)}</span>
        <span aria-hidden="true"> · </span>
        <span>{job.company}</span>
        {job.location ? (
          <>
            <span aria-hidden="true"> · </span>
            <span>{job.location}</span>
          </>
        ) : null}
        {job.remote ? (
          <>
            <span aria-hidden="true"> · </span>
            <span>{t("remote")}</span>
          </>
        ) : null}
      </p>
      <h1 className="font-display">{job.title}</h1>
      <p className="jobs-apply-row">
        <a
          className="jobs-apply"
          href={job.apply_url}
          rel="noopener noreferrer"
          target="_blank"
        >
          {t("apply", { source: sourceLabel(job.source, locale) })}
        </a>
      </p>
      <p className="jobs-attribution">
        {t("attribution")}{" "}
        <a href={job.source_url} rel="noopener noreferrer" target="_blank">
          {job.source_url}
        </a>
      </p>
      {job.description_html ? (
        <div
          className="jobs-body"
          dangerouslySetInnerHTML={{ __html: job.description_html }}
        />
      ) : job.description_text ? (
        <div className="jobs-body">
          <p>{job.description_text}</p>
        </div>
      ) : null}
    </article>
  );
}
