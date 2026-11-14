"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import ApplicationFlow from "@/components/dashboard/ApplicationFlow";
import CreditsStrip from "@/components/dashboard/CreditsStrip";
import MatchesBlock from "@/components/dashboard/MatchesBlock";
import NextActionBand from "@/components/dashboard/NextActionBand";
import OngoingTable from "@/components/dashboard/OngoingTable";
import { nextSteps } from "@/components/dashboard/nextSteps";
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, listResumes } from "@/lib/api";
import type { Application } from "@/lib/applications";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";
import { preferenceSummary } from "@/lib/jobs";
import { readLastVisit, writeLastVisit } from "@/lib/last-visit";

/**
 * The overview: what changed, the one thing to do, and the shape of the search.
 *
 * Four blocks and no free-floating numbers. Every figure that wants studying
 * has moved to the tab that owns it -- the application metrics to
 * Applications, employer views and resume state to Profile, credits to Usage
 * -- because a row of numbers with no subject beside it is something you have
 * to hold in your head on the way down the page. What is left is a line
 * saying what is new, one action, the matches, and the pipeline as a shape
 * with the few rows that need you.
 */
export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tJobs = useTranslations("jobs");
  const format = useFormatter();
  const router = useRouter();

  const [dash, setDash] = useState<Dashboard | null>(null);
  const [rows, setRows] = useState<Application[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [masterId, setMasterId] = useState<string | null>(null);
  // Read once, before the first fetch stamps the new visit, so the "since"
  // line describes the previous visit rather than this one.
  const since = useRef<string | null>(null);
  const [lastVisit, setLastVisit] = useState<Date | null>(null);

  const load = useCallback(
    async (window: string | null) => {
      const next = await fetchDashboard(window);
      setDash(next);
      setRows(next.applications);
      setError(null);
      return next;
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    since.current = readLastVisit();
    if (since.current) {
      const parsed = new Date(since.current);
      if (!Number.isNaN(parsed.getTime())) setLastVisit(parsed);
    }
    load(since.current).catch((err) => {
      if (cancelled) return;
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else setError(err instanceof ApiError ? err.message : t("needLogin"));
    });
    // Stamped after the request is issued, so a reload inside the same second
    // still reports the earlier window rather than an empty one.
    writeLastVisit();
    // The master's id is only needed to route a "publish your resume" step.
    listResumes()
      .then((items) => {
        if (cancelled) return;
        setMasterId(items.find((r) => (r.kind ?? "master") === "master")?.id ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [load, router, t]);

  const summary = useMemo(() => {
    if (!dash) return "";
    const label = (keys: Record<string, string>, v: string) =>
      keys[v] ? tJobs(keys[v] as Parameters<typeof tJobs>[0]) : v;
    return preferenceSummary(dash.preference, {
      seniority: (v) => label(SENIORITY_KEYS, v),
      fn: (v) => label(FUNCTION_KEYS, v),
      country: (v) => label(COUNTRY_KEYS, v),
      remote: tJobs("remote"),
      onsite: tJobs("remoteNo"),
    });
  }, [dash, tJobs]);

  const steps = useMemo(() => {
    if (!dash) return [];
    return nextSteps({
      applications: rows,
      health: dash.resume_health,
      digest: dash.digest,
      hasPreference: summary !== "",
      masterId,
    });
  }, [dash, rows, summary, masterId]);

  const strongFits = useMemo(
    () => (dash?.digest?.items ?? []).filter((i) => (i.fit_score ?? 0) >= 0.8).length,
    [dash],
  );

  function replace(next: Application) {
    setRows((prev) => prev.map((r) => (r.job_id === next.job_id ? next : r)));
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
      <div className="mb-6">
        <h1 className="rr-page-title">{t("overviewTitle")}</h1>
        {dash ? (
          <>
            {/* What changed since you last looked. The reason to open this
                page on a day when nothing else has. */}
            <p className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
              {lastVisit ? (
                <>
                  <span className="text-muted-foreground">
                    {t("sinceLabel", {
                      date: format.relativeTime(lastVisit, new Date()),
                    })}
                  </span>
                  {dash.new_match_count !== null && dash.new_match_count > 0 ? (
                    <span className="font-semibold">
                      {t("sinceNewMatches", { count: dash.new_match_count })}
                    </span>
                  ) : null}
                  {strongFits > 0 ? (
                    <span className="font-semibold">
                      {t("sinceStrongFits", { count: strongFits })}
                    </span>
                  ) : null}
                  {dash.employer_views.disclosure_count > 0 ? (
                    <span className="font-semibold">
                      {t("sinceEmployerViews", {
                        count: dash.employer_views.disclosure_count,
                      })}
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="text-muted-foreground">{t("sinceFirstVisit")}</span>
              )}
            </p>
            <p className="mt-2.5 inline-flex flex-wrap items-center gap-2 rounded-full border border-rule bg-sheet px-3 py-1 text-[0.78rem] text-muted-foreground">
              {summary ? <span>{summary}</span> : <span>{t("matchEmpty")}</span>}
              <Link href="/preferences" className="underline underline-offset-2">
                {t("editPreferences")}
              </Link>
            </p>
          </>
        ) : null}
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {!dash && !error ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : null}

      {dash ? (
        <div className="grid gap-4">
          <NextActionBand
            steps={steps}
            applications={rows}
            onChanged={replace}
            onRebuilt={() => void load(since.current).catch(() => {})}
          />
          <CreditsStrip />
          <MatchesBlock
            digest={dash.digest}
            matches={dash.matches}
            matchCount={dash.match_count}
            newMatchCount={dash.new_match_count}
          />
          <section className="rr-card px-5 py-4">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("headingApplications")}{" "}
                <span className="font-normal text-muted-foreground">
                  · {t("applicationsLead", { tracked: rows.length, needs: steps.length })}
                </span>
              </h2>
              <Link
                href="/dashboard/applications"
                className="text-xs text-muted-foreground underline"
              >
                {t("openStatusTable")}
              </Link>
            </div>
            {/* The shape, then the few rows that need you. No metrics here:
                those are figures you study, and studying has its own tab. */}
            <ApplicationFlow rows={rows} bare />
            <OngoingTable rows={rows} />
          </section>
        </div>
      ) : null}
    </main>
  );
}
