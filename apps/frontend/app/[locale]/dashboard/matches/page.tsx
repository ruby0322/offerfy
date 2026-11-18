"use client";

import { useEffect, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import DigestCard from "@/components/dashboard/DigestCard";
import FreshDeck from "@/components/dashboard/FreshDeck";
import MatchRow from "@/components/dashboard/MatchRow";
import ViewToggle, {
  DASHBOARD_VIEWS,
  type DashboardView,
} from "@/components/dashboard/ViewToggle";
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";
import { groupByDay, preferenceSummary } from "@/lib/jobs";
import { useStoredPreference } from "@/lib/stored-preference";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";

/**
 * Matches, with the weekly scoring and the daily ingest kept apart.
 *
 * These are two different products and the old dashboard blurred them: the
 * match count was coded as a *fallback* for the digest, so a thin week showed
 * a number instead of roles and the two were never on screen together.
 *
 * The picks are scored against your resume by a model, once a week. The fresh
 * roles come off the morning's ingest and nothing has read them, which is why
 * they carry no score -- and saying so is the point, because a posting with
 * no score is unscored, not poor.
 *
 * Two shapes, one choice. Rows are the dense scan; cards are one role to a
 * page, which is what a phone can actually hold -- the row has to truncate
 * the title, the model's sentence AND the gap, which on this page is most of
 * what there is to read. The choice governs both sections together, because
 * it is about how the page is read rather than which half you are in, and it
 * is remembered per browser. Each section keeps its own deck: 8 picks and 12
 * arrivals in one queue would turn into a thick card becoming a thin one
 * halfway through, with no way to skip ahead to the arrivals.
 */

const VIEW_KEY = "offerfy:matches:view";

export default function MatchesPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tJobs = useTranslations("jobs");
  const format = useFormatter();
  const router = useRouter();
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cards on a thumb, rows on a desktop, until the reader says otherwise --
  // and then their choice stands on both. `narrow` is false for the first
  // paint, which costs nothing here: neither view renders before the dashboard
  // has answered, and by then the query has long been readable.
  const narrow = useMediaQuery(NARROW_SCREEN);
  const [view, chooseView] = useStoredPreference<DashboardView>(
    VIEW_KEY,
    DASHBOARD_VIEWS,
    narrow ? "cards" : "list",
  );

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

  const days = useMemo(() => groupByDay(dash?.matches ?? []), [dash]);
  // A switch over a section with nothing in it would switch nothing.
  const hasPicks = (dash?.digest?.items.length ?? 0) > 0;
  const hasFresh = (dash?.matches.length ?? 0) > 0;

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="rr-page-title">{t("headingMatches")}</h1>
          {dash ? (
            <>
              <p className="mt-2 text-sm text-muted-foreground">
                {t("matchesLead", { count: dash.match_count })}
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
        <Link href="/jobs" className="rr-btn rr-btn-ghost">
          {t("browseRoles")}
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {!dash && !error ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : null}

      {dash ? (
        <div className="grid min-w-0 gap-5 [&>*]:min-w-0">
          {/* Cards carry their own chrome, one border per role, so the
              section drops its card: a card of cards is two borders deep and
              reads as a mistake. */}
          <section
            className={view === "cards" ? "min-w-0" : "rr-card min-w-0 px-5 py-4"}
            data-testid="matches-weekly"
          >
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("picksHeading")}{" "}
                <span className="font-normal text-muted-foreground">
                  · {t("picksScoredAgainst")}
                </span>
              </h2>
              {/* Full width once the heading has taken its own line, which at
                  phone width it always does: without it this group is sized to
                  its contents and lands wherever the heading left off, so the
                  switch floats in the middle of the row instead of sitting at
                  the gutter where every other control on the page sits. */}
              <div className="flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-2 wide:w-auto wide:justify-end">
                {dash.digest ? (
                  <p className="text-xs text-muted-foreground">
                    {t("picksBuiltFrom", {
                      date: format.dateTime(new Date(dash.digest.built_at), {
                        month: "short",
                        day: "numeric",
                      }),
                      count: dash.digest.candidate_count,
                    })}
                  </p>
                ) : null}
                {/* Only where there is something to switch. */}
                {hasPicks ? <ViewToggle view={view} choose={chooseView} /> : null}
              </div>
            </div>
            {/* DigestCard owns the tailoring flow and the degraded-build
                notice; a second copy would drift from it. */}
            {dash.digest ? (
              <div className={view === "cards" ? "" : "mt-3"}>
                <DigestCard
                  hideHeading
                  view={view}
                  digest={dash.digest}
                  fallback={
                    <p className="text-sm text-muted-foreground">{t("picksEmpty")}</p>
                  }
                />
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">{t("picksNoneYet")}</p>
            )}
          </section>

          <section
            className={view === "cards" ? "min-w-0" : "rr-card min-w-0 px-5 py-4"}
            data-testid="matches-fresh"
          >
            {/* No second LINE opposite the heading: the scoring rhythm used
                to sit here, and it read as a promise about THESE postings.
                The view switch is a control, not a sentence, and says nothing
                about them. */}
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("freshHeading")}{" "}
                <span className="font-normal text-muted-foreground">
                  · {t("freshFromIngest")}
                </span>
              </h2>
              {/* ms-auto, not justify-between alone: once the heading wraps to
                  its own line the switch is the only thing left on the next
                  one, and justify-between puts a lone item at the start. */}
              {hasFresh ? (
                <span className="ms-auto">
                  <ViewToggle view={view} choose={chooseView} />
                </span>
              ) : null}
            </div>
            {days.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                {dash.preference.updated_at !== null ? t("matchNone") : t("matchEmpty")}
              </p>
            ) : view === "cards" ? (
              <FreshDeck items={dash.matches} totalCount={dash.match_count} />
            ) : (
              days.map(({ day, items }) => (
                <div key={day} className="mt-4">
                  <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
                    {day === "unknown"
                      ? t("freshUndated")
                      : format.dateTime(new Date(day), {
                          weekday: "long",
                          month: "short",
                          day: "numeric",
                        })}{" "}
                    · {items.length}
                  </p>
                  <ul className="mt-1">
                    {items.map((job) => (
                      <MatchRow
                        key={job.id}
                        jobId={job.id}
                        title={job.title}
                        company={job.company}
                        meta={[job.location, job.remote ? tJobs("remote") : null]
                          .filter(Boolean)
                          .join(" · ")}
                        score={null}
                        action={
                          <Link
                            href={`/jobs/${job.id}`}
                            className="rr-btn rr-btn-ghost rr-btn-sm"
                          >
                            {t("open")}
                          </Link>
                        }
                      />
                    ))}
                  </ul>
                </div>
              ))
            )}
          </section>
        </div>
      ) : null}
    </main>
  );
}
