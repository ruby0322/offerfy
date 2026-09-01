"use client";

import { useTranslations } from "next-intl";
import type { JobQuery } from "@/lib/jobs";

type Props = {
  query: JobQuery;
};

export default function JobSearchForm({ query }: Props) {
  const t = useTranslations("jobs");
  return (
    <form className="jobs-filters" method="get">
      <label className="jobs-field">
        <span>{t("queryLabel")}</span>
        <input type="search" name="q" defaultValue={query.q ?? ""} placeholder={t("queryPlaceholder")} />
      </label>
      <label className="jobs-field">
        <span>{t("sourceLabel")}</span>
        <select name="source" defaultValue={query.source ?? ""}>
          <option value="">{t("sourceAll")}</option>
          <option value="greenhouse">Greenhouse</option>
          <option value="lever">Lever</option>
          <option value="ashby">Ashby</option>
          <option value="taiwanjobs">{t("sourceTaiwanjobs")}</option>
        </select>
      </label>
      <label className="jobs-field">
        <span>{t("remoteLabel")}</span>
        <select name="remote" defaultValue={query.remote ?? ""}>
          <option value="">{t("remoteAll")}</option>
          <option value="true">{t("remoteYes")}</option>
          <option value="false">{t("remoteNo")}</option>
        </select>
      </label>
      <button type="submit">{t("submit")}</button>
    </form>
  );
}
