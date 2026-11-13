"use client";

import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import FacetMenu, { type FacetOption } from "@/components/jobs/FacetMenu";
import WherePicker from "@/components/jobs/WherePicker";
import {
  COUNTRY_KEYS,
  FUNCTION_KEYS,
  REGION_KEYS,
  SENIORITY_KEYS,
  type JobsMessageKey,
} from "@/components/jobs/facet-labels";
import { detectCountry } from "@/lib/geo";
import { jobFilterCount, type JobFacetOptions, type JobQuery } from "@/lib/jobs";

type Props = {
  query: JobQuery;
  facets: JobFacetOptions | null;
  /**
   * Query keys a curated board owns. Its facet leaves the bar (the board is
   * that filter) and the board's name is pinned in front of the row instead.
   */
  locked?: (keyof JobQuery)[];
  boardLabel?: string;
  /**
   * Only the open catalogue guesses a country from the device clock. A board
   * page has already said where it is about, and a visitor who followed a
   * shared link has said it even louder.
   */
  detectLocation?: boolean;
};

/** One tab's worth of memory: "this tab has already had its guess". */
const DETECTED_KEY = "offerfy.jobs.located";

export default function FilterBar({
  query,
  facets,
  locked = [],
  boardLabel,
  detectLocation = false,
}: Props) {
  const t = useTranslations("jobs");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const searchBox = useRef<HTMLInputElement>(null);

  const label = (keys: Record<string, JobsMessageKey>, value: string, fallback?: JobsMessageKey) => {
    const key = keys[value];
    if (key) return t(key);
    return fallback ? t(fallback) : value;
  };

  /** The current URL with one key replaced; null removes it. */
  const hrefWith = useMemo(
    () =>
      (changes: Partial<Record<keyof JobQuery, string | null>>): string => {
        const next = new URLSearchParams(params?.toString() ?? "");
        for (const [key, value] of Object.entries(changes)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, value);
        }
        const text = next.toString();
        return text ? `${pathname}?${text}` : pathname;
      },
    [params, pathname],
  );

  // The one place a filter is applied without a click. It runs once per tab,
  // writes the guess into the URL rather than keeping it in memory, and only
  // ever fires when the visitor has expressed no location of their own --
  // so it is visible in the bar, shareable, and undone by one click on
  // "Anywhere". A crawler, which runs no JavaScript, never sees it and is
  // served the whole catalogue, which is what it should index.
  useEffect(() => {
    if (!detectLocation || query.country || query.city) return;
    if (!facets || facets.countries.length === 0) return;
    let seen = false;
    try {
      seen = sessionStorage.getItem(DETECTED_KEY) === "1";
    } catch {
      // Private mode or blocked storage: guess once per page load instead of
      // once per tab. Worse, but not broken.
    }
    if (seen) return;
    try {
      sessionStorage.setItem(DETECTED_KEY, "1");
    } catch {
      /* see above */
    }
    const available = new Set(facets.countries.map((option) => option.value));
    const guess = detectCountry(locale, available);
    if (!guess) return;
    router.replace(hrefWith({ country: guess }), { scroll: false });
  }, [detectLocation, query.country, query.city, facets, locale, router, hrefWith]);

  const isLocked = (key: keyof JobQuery) => locked.includes(key);
  // What the VISITOR narrowed by, not what the board already is: on
  // /jobs/internships the seniority filter is the page, and offering to
  // "clear 1 filter" when they have set nothing reads as a bug.
  const visitorQuery = { ...query };
  for (const key of locked) delete visitorQuery[key];
  const count = jobFilterCount(visitorQuery);

  const options = (
    raw: { value: string; count: number }[] | undefined,
    keys: Record<string, JobsMessageKey>,
    fallback?: JobsMessageKey,
  ): FacetOption[] =>
    (raw ?? []).map((option) => ({
      value: option.value,
      label: label(keys, option.value, fallback),
      count: option.count,
    }));

  const functions = options(facets?.functions, FUNCTION_KEYS, "functionOther");
  const seniorities = options(facets?.seniorities, SENIORITY_KEYS, "seniorityUnknown");
  const regions = options(facets?.regions, REGION_KEYS);
  const companies: FacetOption[] = (facets?.companies ?? []).map((option) => ({
    value: option.value,
    label: option.value,
    count: option.count,
  }));

  const posted: FacetOption[] = [
    { value: "1", label: t("posted1") },
    { value: "7", label: t("posted7") },
    { value: "30", label: t("posted30") },
  ];
  const remote: FacetOption[] = [
    { value: "true", label: t("remoteYes") },
    { value: "false", label: t("remoteNo") },
  ];

  const selectedOf = (list: FacetOption[], value?: string) =>
    value ? (list.find((option) => option.value === value)?.label ?? value) : undefined;

  return (
    <div className="jobs-controls">
      {/* GET, so a search is a normal navigation with a URL. The other
          filters ride along as hidden inputs -- without them, searching
          would silently drop every facet the visitor had set. */}
      <form className="jobs-search" method="get" action={pathname}>
        {Array.from(params?.entries() ?? [])
          .filter(([key]) => key !== "q")
          .map(([key, value]) => (
            <input key={`${key}-${value}`} type="hidden" name={key} value={value} />
          ))}
        <input
          ref={searchBox}
          type="search"
          name="q"
          defaultValue={query.q ?? ""}
          placeholder={t("queryPlaceholder")}
          aria-label={t("queryLabel")}
        />
        {isLocked("country") ? null : (
          <WherePicker
            country={query.country}
            city={query.city}
            countries={facets?.countries ?? []}
            countryLabel={(code) => label(COUNTRY_KEYS, code)}
            hrefFor={({ country, city }) => hrefWith({ country, city })}
            detected={detectLocation}
          />
        )}
        <button type="submit">{t("submit")}</button>
      </form>

      <div className="jobs-facets">
        {boardLabel ? (
          <span className="jobs-facet-board">
            {boardLabel}
            <span className="jobs-facet-board-tag">{t("filterBoard")}</span>
          </span>
        ) : null}

        {isLocked("function") ? null : (
          <FacetMenu
            heading={t("filterFunction")}
            label={t("filterFunction")}
            anyLabel={t("filterAny")}
            clearLabel={t("filterFunction")}
            selected={selectedOf(functions, query.function)}
            value={query.function}
            options={functions}
            wide
            clearHref={hrefWith({ function: null })}
            hrefFor={(value) => hrefWith({ function: value })}
          />
        )}

        {isLocked("seniority") ? null : (
          <FacetMenu
            heading={t("filterLevel")}
            label={t("filterLevel")}
            anyLabel={t("filterAny")}
            clearLabel={t("filterLevel")}
            selected={selectedOf(seniorities, query.seniority)}
            value={query.seniority}
            options={seniorities}
            clearHref={hrefWith({ seniority: null })}
            hrefFor={(value) => hrefWith({ seniority: value })}
          />
        )}

        {isLocked("postedWithinDays") ? null : (
          <FacetMenu
            heading={t("postedLabel")}
            label={t("filterPosted")}
            anyLabel={t("postedAll")}
            clearLabel={t("filterPosted")}
            selected={selectedOf(posted, query.postedWithinDays)}
            value={query.postedWithinDays}
            options={posted}
            clearHref={hrefWith({ postedWithinDays: null })}
            hrefFor={(value) => hrefWith({ postedWithinDays: value })}
          />
        )}

        {isLocked("remote") ? null : (
          <FacetMenu
            heading={t("filterRemote")}
            label={t("filterRemote")}
            anyLabel={t("remoteAll")}
            clearLabel={t("filterRemote")}
            selected={selectedOf(remote, query.remote)}
            value={query.remote}
            options={remote}
            clearHref={hrefWith({ remote: null })}
            hrefFor={(value) => hrefWith({ remote: value })}
          />
        )}

        {isLocked("company") ? null : (
          <FacetMenu
            heading={t("filterEmployer")}
            label={t("filterEmployer")}
            anyLabel={t("filterAny")}
            clearLabel={t("filterEmployer")}
            selected={query.company}
            value={query.company}
            options={companies}
            findLabel={t("findEmployer")}
            footer={
              facets?.employer_total ? (
                <span className="jobs-menu-note">
                  {t("allEmployersCount", { count: facets.employer_total })}
                </span>
              ) : null
            }
            clearHref={hrefWith({ company: null })}
            hrefFor={(value) => hrefWith({ company: value })}
          />
        )}

        {/* Region is here rather than in the bar because country answers the
            same question better; it stays reachable for "anywhere in APAC". */}
        {isLocked("region") || regions.length === 0 ? null : (
          <FacetMenu
            heading={t("regionLabel")}
            label={t("filterMore")}
            anyLabel={t("regionAll")}
            clearLabel={t("regionLabel")}
            selected={selectedOf(regions, query.region)}
            value={query.region}
            options={regions}
            clearHref={hrefWith({ region: null })}
            hrefFor={(value) => hrefWith({ region: value })}
          />
        )}
      </div>

      {count > 0 ? (
        <p className="jobs-clear">
          <Link href={pathname}>{t("clearFilters", { count })}</Link>
        </p>
      ) : null}
    </div>
  );
}
