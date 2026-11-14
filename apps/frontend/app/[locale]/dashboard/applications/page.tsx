"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import ApplicationFlow from "@/components/dashboard/ApplicationFlow";
import ApplicationMetrics from "@/components/dashboard/ApplicationMetrics";
import ApplicationTable from "@/components/dashboard/ApplicationTable";
import { nextAction } from "@/components/dashboard/applications";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";

/**
 * The applications page: what is true, then how it got that way, then every
 * row.
 *
 * Its own route rather than a section of /dashboard, because the three
 * panels are things you come here to study -- the overview keeps the shape
 * and the five rows that need you, and links through to this.
 *
 * It reads the same one-request dashboard payload the overview does, so
 * arriving here from there costs nothing new and the two can never show
 * different numbers for the same pipeline.
 */
export default function ApplicationsPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDashboard()
      .then((next) => {
        if (!cancelled) {
          setDash(next);
          setError(null);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : t("needLogin"));
      });
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  // Its own memo so the identity is stable across renders; `?? []` inline
  // would make a new array every time and rebuild everything below it.
  const rows = useMemo(() => dash?.applications ?? [], [dash]);
  const needs = useMemo(
    () => rows.filter((app) => nextAction(app) !== null).length,
    [rows],
  );

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-5 sm:py-12">
        <div className="mb-7 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="rr-page-title">{t("headingApplications")}</h1>
            {dash ? (
              <p className="mt-2 text-sm text-muted-foreground">
                {t("applicationsLead", { tracked: rows.length, needs })}
              </p>
            ) : null}
          </div>
          <Link href="/dashboard" className="rr-btn rr-btn-ghost">
            {t("title")}
          </Link>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {!dash && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}

        {dash ? (
          <div className="grid gap-5">
            <ApplicationMetrics rows={rows} />
            <ApplicationFlow rows={rows} />
            {/* Keyed on the payload so a refetch rebuilds the table's own
                row state rather than leaving it holding stale rows. */}
            <ApplicationTable key={rows.length} rows={rows} />
          </div>
        ) : null}
      </main>
      <Footer variant="app" />
    </div>
  );
}
