import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import type { AppLocale } from "@/i18n/routing";
import CuratedBoards from "@/components/jobs/CuratedBoards";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import FilterBar from "@/components/jobs/FilterBar";
import JobPulseBoard from "@/components/jobs/JobPulseBoard";
import JobResults from "@/components/jobs/JobResults";
import { FUNCTION_KEYS } from "@/components/jobs/facet-labels";
import { boardQuery, type JobBoard } from "@/lib/job-boards";
import {
  type JobFacetOption,
  type JobFacetOptions,
  type JobList,
  type JobPulse,
  type JobQuery,
} from "@/lib/jobs";

type Props = {
  locale: AppLocale;
  board: JobBoard;
  /** What the visitor set on top of the board, already merged for fetching. */
  query: JobQuery;
  visitorQuery: JobQuery;
  list: JobList;
  facets: JobFacetOptions | null;
  pulse: JobPulse | null;
  cities: JobFacetOption[];
  boardCounts: Record<string, number | undefined>;
};

/**
 * One template for all six curated boards.
 *
 * Everything on it is scoped to the board — the pulse strip, the facet
 * counts, the accent block — because all three come from endpoints that take
 * the same filters the board is made of. The only thing that varies between
 * boards is which accent block the config asks for.
 */
export default async function JobBoardPage({
  locale,
  board,
  query,
  visitorQuery,
  list,
  facets,
  pulse,
  cities,
  boardCounts,
}: Props) {
  const t = await getTranslations("jobs");
  const title = t(board.titleKey);

  const topFunctions = (facets?.functions ?? [])
    .slice()
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  return (
    <div className="jobs-index mx-auto max-w-[72rem] px-5">
      <p className="jobs-crumb">
        <Link href="/jobs">{t("title")}</Link>
        <span aria-hidden="true"> › </span>
        <span>{title}</span>
      </p>
      <h1 className="font-display">{title}</h1>
      <p className="jobs-lead">{t(board.leadKey)}</p>

      {pulse ? <JobPulseBoard pulse={pulse} boards={false} /> : null}

      {board.accent === "employers" && pulse && pulse.top_employers.length > 0 ? (
        <section className="jobs-accent">
          <h2 className="jobs-section-label">{t("boardEmployersHeading")}</h2>
          <div className="jobs-logo-row">
            {pulse.top_employers.map((employer) => (
              <Link
                key={employer.company}
                href={`/jobs/${board.slug}?company=${encodeURIComponent(employer.company)}`}
                className="jobs-logo-chip"
              >
                <EmployerLogo company={employer.company} size="sm" />
                {employer.company}
                <span className="jobs-opt-n">{employer.open_count.toLocaleString()}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {board.accent === "cities" && cities.length > 0 ? (
        <section className="jobs-accent">
          <h2 className="jobs-section-label">{t("boardCitiesHeading")}</h2>
          <div className="jobs-where-pills">
            {cities.map((option) => (
              <Link
                key={option.value}
                href={`/jobs/${board.slug}?city=${encodeURIComponent(option.value)}`}
                className={`jobs-pill${option.value === visitorQuery.city ? " is-on" : ""}`}
              >
                {option.value}
                <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {board.accent === "functions" && topFunctions.length > 0 ? (
        <section className="jobs-accent">
          <h2 className="jobs-section-label">{t("boardFunctionsHeading")}</h2>
          <div className="jobs-where-pills">
            {topFunctions.map((option) => (
              <Link
                key={option.value}
                href={`/jobs/${board.slug}?function=${encodeURIComponent(option.value)}`}
                className={`jobs-pill${option.value === visitorQuery.function ? " is-on" : ""}`}
              >
                {t(FUNCTION_KEYS[option.value] ?? "functionOther")}
                <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <FilterBar
        query={boardQuery(board, visitorQuery)}
        facets={facets}
        locked={board.locked}
        boardLabel={title}
      />

      {list.items.length === 0 ? (
        <p className="blog-empty">{t("emptyFiltered")}</p>
      ) : (
        <>
          <p className="jobs-count">{t("resultCount", { count: list.total })}</p>
          <JobResults locale={locale} query={query} initial={list} />
        </>
      )}

      <CuratedBoards counts={boardCounts} exclude={board.slug} compact />
    </div>
  );
}
