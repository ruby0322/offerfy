"use client";

import { useFormatter, useTranslations } from "next-intl";
import type { EmployerViews as EmployerViewsData } from "@/lib/dashboard";

export default function EmployerViews({ views }: { views: EmployerViewsData }) {
  const t = useTranslations("dashboard");
  const format = useFormatter();
  const last = views.last_disclosed_at ? new Date(views.last_disclosed_at) : null;
  const lastValid = last !== null && !Number.isNaN(last.getTime());
  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-employer-views" aria-labelledby="dash-employers">
      <h2 id="dash-employers" className="text-sm font-semibold tracking-tight">
        {t("headingEmployerViews")}
      </h2>
      {views.disclosure_count === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{t("employerViewsEmpty")}</p>
      ) : (
        <>
          <p className="mt-2 text-sm font-medium">
            {t("employerViewsCount", { count: views.disclosure_count })}
          </p>
          {lastValid ? (
            <p className="mt-1 text-xs text-muted-foreground">
              {t("employerViewsLast", {
                date: format.dateTime(last, { dateStyle: "medium" }),
              })}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
