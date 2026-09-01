import { getTranslations } from "next-intl/server";
import type { AppLocale } from "@/i18n/routing";
import { hasJobFilters, type JobList, type JobListItem, type JobQuery } from "@/lib/jobs";
import JobSearchForm from "@/components/jobs/JobSearchForm";
import JobResults from "@/components/jobs/JobResults";
import JobsMarquee from "@/components/jobs/JobsMarquee";

type Props = {
  locale: AppLocale;
  query: JobQuery;
  list: JobList;
  featured: JobListItem[];
};

export default async function JobIndex({ locale, query, list, featured }: Props) {
  const t = await getTranslations("jobs");
  const emptyKey = hasJobFilters(query) ? "emptyFiltered" : "emptyCatalog";

  return (
    <div className="jobs-index mx-auto max-w-[72rem] px-5">
      <div className="blog-kicker" aria-hidden="true" />
      <h1 className="font-display">{t("title")}</h1>
      <p className="jobs-lead">{t("lead")}</p>
      {featured.length > 0 ? <JobsMarquee locale={locale} jobs={featured} /> : null}
      <JobSearchForm query={query} />
      {list.items.length === 0 ? (
        <p className="blog-empty">{t(emptyKey)}</p>
      ) : (
        <JobResults locale={locale} query={query} initial={list} />
      )}
    </div>
  );
}
