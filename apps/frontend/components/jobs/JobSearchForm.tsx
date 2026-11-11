"use client";

import { useTranslations } from "next-intl";
import type { JobFacetOptions, JobQuery } from "@/lib/jobs";

type Props = {
  query: JobQuery;
  facets: JobFacetOptions | null;
};

type JobsMessageKey = Parameters<ReturnType<typeof useTranslations<"jobs">>>[0];

const SENIORITY_KEYS: Record<string, JobsMessageKey> = {
  intern: "seniorityIntern",
  new_grad: "seniorityNewGrad",
  junior: "seniorityJunior",
  mid: "seniorityMid",
  senior: "senioritySenior",
  staff_plus: "seniorityStaffPlus",
  manager: "seniorityManager",
  unknown: "seniorityUnknown",
};

const FUNCTION_KEYS: Record<string, JobsMessageKey> = {
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

  return (
    <form className="jobs-filters" method="get">
      <label className="jobs-field">
        <span>{t("queryLabel")}</span>
        <input
          type="search"
          name="q"
          defaultValue={query.q ?? ""}
          placeholder={t("queryPlaceholder")}
          className="text-base md:text-sm"
        />
      </label>
      <label className="jobs-field">
        <span>{t("remoteLabel")}</span>
        <select name="remote" defaultValue={query.remote ?? ""} className="text-base md:text-sm">
          <option value="">{t("remoteAll")}</option>
          <option value="true">{t("remoteYes")}</option>
          <option value="false">{t("remoteNo")}</option>
        </select>
      </label>
      {facets && facets.countries.length > 0 ? (
        <label className="jobs-field">
          <span>{t("countryLabel")}</span>
          <select name="country" defaultValue={query.country ?? ""} className="text-base md:text-sm">
            <option value="">{t("countryAll")}</option>
            {facets.countries.map((option) => (
              <option key={option.value} value={option.value}>
                {option.value}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {facets && facets.regions.length > 0 ? (
        <label className="jobs-field">
          <span>{t("regionLabel")}</span>
          <select name="region" defaultValue={query.region ?? ""} className="text-base md:text-sm">
            <option value="">{t("regionAll")}</option>
            {facets.regions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.value}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {facets && facets.seniorities.length > 0 ? (
        <label className="jobs-field">
          <span>{t("seniorityLabel")}</span>
          <select name="seniority" defaultValue={query.seniority ?? ""} className="text-base md:text-sm">
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
          <select name="function" defaultValue={query.function ?? ""} className="text-base md:text-sm">
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
        <select name="postedWithinDays" defaultValue={query.postedWithinDays ?? ""} className="text-base md:text-sm">
          <option value="">{t("postedAll")}</option>
          <option value="7">{t("posted7")}</option>
          <option value="30">{t("posted30")}</option>
        </select>
      </label>
      <button type="submit">{t("submit")}</button>
    </form>
  );
}
