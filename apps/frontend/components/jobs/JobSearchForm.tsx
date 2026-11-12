"use client";

import { useTranslations } from "next-intl";
import type { JobFacetOptions, JobQuery } from "@/lib/jobs";

type Props = {
  query: JobQuery;
  facets: JobFacetOptions | null;
};

export type JobsMessageKey = Parameters<ReturnType<typeof useTranslations<"jobs">>>[0];

export const COUNTRY_KEYS: Record<string, JobsMessageKey> = {
  AU: "countryAu",
  CA: "countryCa",
  CH: "countryCh",
  CN: "countryCn",
  DE: "countryDe",
  FR: "countryFr",
  GB: "countryGb",
  HK: "countryHk",
  IE: "countryIe",
  IL: "countryIl",
  IN: "countryIn",
  JP: "countryJp",
  KR: "countryKr",
  NL: "countryNl",
  PL: "countryPl",
  SG: "countrySg",
  TW: "countryTw",
  US: "countryUs",
};

const REGION_KEYS: Record<string, JobsMessageKey> = {
  north_america: "regionNorthAmerica",
  emea: "regionEmea",
  apac: "regionApac",
};

export const SENIORITY_KEYS: Record<string, JobsMessageKey> = {
  intern: "seniorityIntern",
  new_grad: "seniorityNewGrad",
  junior: "seniorityJunior",
  mid: "seniorityMid",
  senior: "senioritySenior",
  staff_plus: "seniorityStaffPlus",
  manager: "seniorityManager",
  unknown: "seniorityUnknown",
};

export const FUNCTION_KEYS: Record<string, JobsMessageKey> = {
  software: "functionSoftware",
  backend: "functionBackend",
  frontend: "functionFrontend",
  fullstack: "functionFullstack",
  ml: "functionMl",
  data: "functionData",
  infra: "functionInfra",
  security: "functionSecurity",
  hardware: "functionHardware",
  product: "functionProduct",
  design: "functionDesign",
  other: "functionOther",
};

export default function JobSearchForm({ query, facets }: Props) {
  const t = useTranslations("jobs");

  // Both vocabularies are closed (18 countries, 3 regions), but a value that
  // arrives from the API ahead of a message-file update must still render
  // something rather than throw -- so the fallback is the raw value itself,
  // not another translation key (unlike seniority/function, which fall back
  // to an "unknown"/"other" key because those buckets have no raw value a
  // visitor could read).
  function labeled(keys: Record<string, JobsMessageKey>, value: string): string {
    const key = keys[value];
    return key ? t(key) : value;
  }

  return (
    <form className="jobs-filters" method="get">
      <label className="jobs-field">
        <span>{t("queryLabel")}</span>
        <input
          type="search"
          name="q"
          defaultValue={query.q ?? ""}
          placeholder={t("queryPlaceholder")}
        />
      </label>
      <label className="jobs-field">
        <span>{t("remoteLabel")}</span>
        <select name="remote" defaultValue={query.remote ?? ""}>
          <option value="">{t("remoteAll")}</option>
          <option value="true">{t("remoteYes")}</option>
          <option value="false">{t("remoteNo")}</option>
        </select>
      </label>
      {facets && facets.countries.length > 0 ? (
        <label className="jobs-field">
          <span>{t("countryLabel")}</span>
          <select name="country" defaultValue={query.country ?? ""}>
            <option value="">{t("countryAll")}</option>
            {facets.countries.map((option) => (
              <option key={option.value} value={option.value}>
                {labeled(COUNTRY_KEYS, option.value)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {facets && facets.regions.length > 0 ? (
        <label className="jobs-field">
          <span>{t("regionLabel")}</span>
          <select name="region" defaultValue={query.region ?? ""}>
            <option value="">{t("regionAll")}</option>
            {facets.regions.map((option) => (
              <option key={option.value} value={option.value}>
                {labeled(REGION_KEYS, option.value)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {facets && facets.seniorities.length > 0 ? (
        <label className="jobs-field">
          <span>{t("seniorityLabel")}</span>
          <select name="seniority" defaultValue={query.seniority ?? ""}>
            <option value="">{t("seniorityAll")}</option>
            {facets.seniorities.map((option) => (
              <option key={option.value} value={option.value}>
                {t(SENIORITY_KEYS[option.value] ?? "seniorityUnknown")}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {facets && facets.functions.length > 0 ? (
        <label className="jobs-field">
          <span>{t("functionLabel")}</span>
          <select name="function" defaultValue={query.function ?? ""}>
            <option value="">{t("functionAll")}</option>
            {facets.functions.map((option) => (
              <option key={option.value} value={option.value}>
                {t(FUNCTION_KEYS[option.value] ?? "functionOther")}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className="jobs-field">
        <span>{t("postedLabel")}</span>
        <select name="postedWithinDays" defaultValue={query.postedWithinDays ?? ""}>
          <option value="">{t("postedAll")}</option>
          <option value="7">{t("posted7")}</option>
          <option value="30">{t("posted30")}</option>
        </select>
      </label>
      <button type="submit">{t("submit")}</button>
    </form>
  );
}
