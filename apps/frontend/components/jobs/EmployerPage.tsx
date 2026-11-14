import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import type { AppLocale } from "@/i18n/routing";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import FilterBar from "@/components/jobs/FilterBar";
import JobResults from "@/components/jobs/JobResults";
import { FUNCTION_KEYS } from "@/components/jobs/facet-labels";
import { EMPLOYERS_HREF } from "@/lib/employers";
import {
  sourceLabel,
  type JobEmployerProfile,
  type JobFacetOptions,
  type JobList,
  type JobQuery,
} from "@/lib/jobs";

type Props = {
  locale: AppLocale;
  profile: JobEmployerProfile;
  /** The employer's own filter, merged with whatever the visitor set. */
  query: JobQuery;
  visitorQuery: JobQuery;
  list: JobList;
  facets: JobFacetOptions | null;
};

/** Which accents are worth showing at all: one chip is not a distribution. */
const ACCENT_MIN = 2;
const ACCENT_MAX = 6;

/**
 * "Tracked since Jun 2026" -- when Offerfy first saw one of their postings.
 *
 * Month precision on purpose: the day we started polling a board is an
 * accident of when it was added, and a visitor reading an exact date would
 * reasonably take it for something about the employer.
 *
 * UTC-pinned like Timestamps' `absolute`, so the server pass and the
 * hydration pass agree. Unlike the ages in a job row this says nothing about
 * *now*, so it needs no client swap.
 */
function trackedSince(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(iso));
}

/**
 * One employer: their counts, the shape of their hiring, and their postings.
 *
 * Structurally a curated board (JobBoardPage) with `company` as the locked
 * axis -- the filter bar, the facet counts and the list all come from the
 * same endpoints with the same query, so they cannot disagree. What the
 * boards do not have is the header: an employer is a thing a visitor has an
 * opinion about, and "142 open, 9 this week, hiring in 4 cities" is the part
 * of that opinion the catalog can actually answer.
 *
 * Everything on it is counted from their own postings. There is no company
 * record behind this page -- no blurb, no headcount, no logo beyond the
 * self-hosted mark -- and nothing here should imply one.
 */
export default async function EmployerPage({
  locale,
  profile,
  query,
  visitorQuery,
  list,
  facets,
}: Props) {
  const t = await getTranslations("jobs");
  const cities = profile.cities.slice(0, ACCENT_MAX);
  const functions = profile.functions.slice(0, ACCENT_MAX);
  const sources = profile.sources.map((source) => sourceLabel(source, locale)).join(" · ");

  return (
    <div className="jobs-index mx-auto max-w-[72rem] px-5">
      <p className="jobs-crumb">
        <Link href="/jobs">{t("title")}</Link>
        <span aria-hidden="true"> › </span>
        <Link href={EMPLOYERS_HREF}>{t("employersTitle")}</Link>
        <span aria-hidden="true"> › </span>
        <span>{profile.company}</span>
      </p>

      <div className="jobs-employer-head">
        <EmployerLogo company={profile.company} />
        <div>
          <h1 className="font-display">{profile.company}</h1>
          <p className="jobs-lead">{t("employerLead", { count: profile.open_count })}</p>
        </div>
      </div>

      <dl className="jobs-stats">
        <div className="jobs-stat">
          <dt>{t("employerStatOpen")}</dt>
          <dd>{profile.open_count.toLocaleString()}</dd>
        </div>
        <div className="jobs-stat">
          <dt>{t("employerStatNew")}</dt>
          <dd>{profile.new_count.toLocaleString()}</dd>
        </div>
        {/* Named "remote-flagged", not "remote": this is the employer's own
            flag on their board, which plenty of boards never set, so it
            undercounts rather than guessing from the description. Same
            caveat the /jobs/remote board carries. */}
        <div className="jobs-stat">
          <dt>{t("employerStatRemote")}</dt>
          <dd>{profile.remote_count.toLocaleString()}</dd>
        </div>
        {profile.first_seen_at ? (
          <div className="jobs-stat">
            <dt>{t("employerStatTracked")}</dt>
            <dd>{trackedSince(profile.first_seen_at, locale)}</dd>
          </div>
        ) : null}
      </dl>

      {cities.length >= ACCENT_MIN ? (
        <section className="jobs-accent">
          <h2 className="jobs-section-label">{t("employerWhere")}</h2>
          <div className="jobs-where-pills">
            {cities.map((option) => (
              <Link
                key={option.value}
                href={`${EMPLOYERS_HREF}/${profile.slug}?city=${encodeURIComponent(option.value)}`}
                className={`jobs-pill${option.value === visitorQuery.city ? " is-on" : ""}`}
              >
                {option.value}
                <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {functions.length >= ACCENT_MIN ? (
        <section className="jobs-accent">
          <h2 className="jobs-section-label">{t("employerWhat")}</h2>
          <div className="jobs-where-pills">
            {functions.map((option) => (
              <Link
                key={option.value}
                href={`${EMPLOYERS_HREF}/${profile.slug}?function=${encodeURIComponent(option.value)}`}
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
        query={query}
        facets={facets}
        locked={["company"]}
        boardLabel={profile.company}
      />

      {list.items.length === 0 ? (
        <p className="blog-empty">{t("emptyFiltered")}</p>
      ) : (
        <>
          <p className="jobs-count">{t("resultCount", { count: list.total })}</p>
          <JobResults locale={locale} query={query} initial={list} />
        </>
      )}

      <p className="jobs-employer-foot">
        {/* Named, not linked: Job stores the posting's URL, never the board's,
            so a board link here would have to be guessed from the source. */}
        {sources ? <span>{t("employerSources", { sources })}</span> : null}
        <Link href={EMPLOYERS_HREF}>{t("employersAll")}</Link>
      </p>
    </div>
  );
}
