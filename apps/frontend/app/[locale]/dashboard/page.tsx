"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import ArrivalsBlock from "@/components/dashboard/ArrivalsBlock";
import ApplicationFlow from "@/components/dashboard/ApplicationFlow";
import CreditsStrip from "@/components/dashboard/CreditsStrip";
import MatchesBlock from "@/components/dashboard/MatchesBlock";
import NextActionBand from "@/components/dashboard/NextActionBand";
import OnboardingCard from "@/components/dashboard/OnboardingCard";
import OngoingTable from "@/components/dashboard/OngoingTable";
import ResumesBlock from "@/components/dashboard/ResumesBlock";
import { arrivalHeadline, strongArrivalCount } from "@/components/dashboard/arrivals";
import { nextSteps } from "@/components/dashboard/nextSteps";
import { cardVisible } from "@/components/dashboard/onboarding";
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, listResumes, type Resume } from "@/lib/api";
import type { Application } from "@/lib/applications";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";
import { hasSavedPreference, markMatchesSeen, preferenceSummary } from "@/lib/jobs";

/**
 * The overview: what changed, the one thing to do, and the shape of the search.
 *
 * A few blocks and no free-floating numbers. Every figure that wants studying
 * has moved to the tab that owns it -- the application metrics to
 * Applications, employer views and resume state to Profile, credits to Usage
 * -- because a row of numbers with no subject beside it is something you have
 * to hold in your head on the way down the page. What is left is a line
 * saying what is new, one action, what landed and what matches, and the pipeline as a shape
 * with the few rows that need you.
 */
export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tJobs = useTranslations("jobs");
  const router = useRouter();

  const [dash, setDash] = useState<Dashboard | null>(null);
  const [rows, setRows] = useState<Application[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Null until the list lands, and after a failure: ResumesBlock reads that
  // as "say nothing" rather than as "you have no resumes".
  const [resumes, setResumes] = useState<Resume[] | null>(null);

  // The server decides what is new and when this person last looked, so
  // there is nothing to remember here: a reload asks the same question.
  //
  // This page is the one that renders ArrivalsBlock, so it is the one that
  // says the arrivals were shown -- the dashboard GET deliberately no longer
  // stamps, because Applications, Matches and Profile fetch it too and show
  // no arrivals at all. Said here rather than in the effect so that a
  // refetch which brings NEW arrivals (after the first run, after a rebuild)
  // marks those too; each call is one UPDATE. Fire-and-forget: the rows are
  // already on screen, and a lost stamp only repeats one window.
  const apply = useCallback((next: Dashboard) => {
    setDash(next);
    setRows(next.applications);
    setError(null);
    void markMatchesSeen().catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchDashboard()
      .then((next) => {
        if (!cancelled) apply(next);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : t("needLogin"));
      });
    // One call, two readers: the "publish your resume" step needs the
    // master's id, and the resumes block needs the rest of the list.
    listResumes()
      .then((items) => {
        if (!cancelled) setResumes(items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apply, router, t]);

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

  const masterId = useMemo(
    () => resumes?.find((r) => (r.kind ?? "master") === "master")?.id ?? null,
    [resumes],
  );

  // Before the first save the preference is a computed seed, so a non-empty
  // summary no longer means "they said what they want". `updated_at` does.
  const showCard = dash ? cardVisible(dash.preference, dash.resume_health) : false;

  const steps = useMemo(() => {
    if (!dash) return [];
    return nextSteps({
      applications: rows,
      health: dash.resume_health,
      digest: dash.digest,
      hasPreference: hasSavedPreference(dash.preference),
      cardOnPage: showCard,
      masterId,
    });
  }, [dash, rows, masterId, showCard]);

  // The header line and the arrivals block read the same list, so the count
  // up here and the rows below cannot disagree.
  const headline = useMemo(
    () => (dash ? arrivalHeadline(dash.arrivals.length, dash.first_visit) : null),
    [dash],
  );
  const strongArrivals = useMemo(
    () => strongArrivalCount(dash?.arrivals ?? []),
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
            {/* What landed since you last looked, as the server measured it.
                A first visit is its own sentence: the server's NULL marker
                means nobody has looked yet, which is not the same thing as
                "welcome back". Strong fits are folded into the same line
                rather than counted again by the block below. */}
            <p className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
              {headline?.kind === "first_visit" ? (
                <span className="text-muted-foreground">
                  {t("sinceFirstArrivals", { count: headline.count })}
                </span>
              ) : headline?.kind === "arrivals" ? (
                <>
                  <span className="font-semibold">
                    {t("sinceArrivals", { count: headline.count })}
                  </span>
                  {strongArrivals > 0 ? (
                    <span className="font-semibold">
                      {t("sinceStrongFits", { count: strongArrivals })}
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="text-muted-foreground">{t("sinceNothingNew")}</span>
              )}
            </p>
            {/* Not while the card is up: until the first save `summary` is
                the resume's guess, and a pill saying "senior · backend" would
                present a guess as something the person chose. */}
            {showCard ? null : (
              <p className="mt-2.5 inline-flex flex-wrap items-center gap-2 rounded-full border border-rule bg-sheet px-3 py-1 text-[0.78rem] text-muted-foreground">
                {hasSavedPreference(dash.preference) ? (
                  <span>{summary}</span>
                ) : (
                  <span>{t("matchEmpty")}</span>
                )}
                <Link href="/preferences" className="underline underline-offset-2">
                  {t("editPreferences")}
                </Link>
              </p>
            )}
          </>
        ) : null}
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {!dash && !error ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : null}

      {dash ? (
        <div className="grid min-w-0 gap-4 [&>*]:min-w-0">
          {showCard ? (
            <OnboardingCard
              pref={dash.preference}
              // Returns the promise: the card must learn that the refetch failed.
              onDone={() => fetchDashboard().then(apply)}
            />
          ) : null}
          {/* "Nothing to do" under a card that is asking for something would
              contradict it. */}
          {showCard && steps.length === 0 ? null : (
            <NextActionBand
              steps={steps}
              applications={rows}
              onChanged={replace}
              onRebuilt={() => void fetchDashboard().then(apply).catch(() => {})}
            />
          )}
          <CreditsStrip />
          <ArrivalsBlock arrivals={dash.arrivals} />
          <MatchesBlock
            digest={dash.digest}
            matches={dash.matches}
            matchCount={dash.match_count}
            hasPreference={hasSavedPreference(dash.preference)}
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
            <OngoingTable rows={rows} onChanged={replace} />
          </section>
          {/* Last, under the pipeline it feeds. It opened the page until
              now, which put the document ahead of everything the document
              is FOR -- today's one move, the matches, the applications --
              and made a page about what to do next begin with housekeeping.
              Read bottom-up it is the closing note instead: this is the
              thing all of the above came out of. */}
          <ResumesBlock
            resumes={resumes}
            applications={rows}
            employerViews={dash.employer_views.disclosure_count}
          />
        </div>
      ) : null}
    </main>
  );
}
