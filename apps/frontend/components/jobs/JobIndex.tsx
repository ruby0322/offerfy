import { getTranslations } from "next-intl/server";
import type { AppLocale } from "@/i18n/routing";
import {
  hasJobFilters,
  type JobFacetOptions,
  type JobList,
  type JobPulse,
  type JobQuery,
} from "@/lib/jobs";
import CuratedBoards from "@/components/jobs/CuratedBoards";
import FilterBar from "@/components/jobs/FilterBar";
import JobPulseBoard from "@/components/jobs/JobPulseBoard";
import JobResults from "@/components/jobs/JobResults";

type Props = {
  locale: AppLocale;
  query: JobQuery;
  list: JobList;
  facets: JobFacetOptions | null;
  pulse: JobPulse | null;
  boardCounts: Record<string, number | undefined>;
};

export default async function JobIndex({
  locale,
  query,
  list,
  facets,
  pulse,
  boardCounts,
}: Props) {
  const t = await getTranslations("jobs");
  const emptyKey = hasJobFilters(query) ? "emptyFiltered" : "emptyCatalog";

  return (
    <div className="jobs-index mx-auto max-w-[72rem] px-5">
      <div className="blog-kicker" aria-hidden="true" />
      <h1 className="font-display">{t("title")}</h1>
      <p className="jobs-lead">{t("lead")}</p>

      {pulse ? <JobPulseBoard pulse={pulse} /> : null}
      <CuratedBoards counts={boardCounts} />

      <FilterBar query={query} facets={facets} detectLocation />

      {list.items.length === 0 ? (
        <p className="blog-empty">{t(emptyKey)}</p>
      ) : (
        <>
          <p className="jobs-count">{t("resultCount", { count: list.total })}</p>
          <JobResults locale={locale} query={query} initial={list} />
        </>
      )}
    </div>
  );
}
