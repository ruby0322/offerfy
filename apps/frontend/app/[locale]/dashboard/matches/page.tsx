"use client";

import { useEffect, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import DigestCard from "@/components/dashboard/DigestCard";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";
import { preferenceSummary, type JobListItem } from "@/lib/jobs";

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
 */

/** Postings arriving on the same calendar day, newest day first. */
function groupByDay(items: JobListItem[]): { day: string; items: JobListItem[] }[] {
  const by = new Map<string, JobListItem[]>();
  for (const job of items) {
    // posted_at is absent for most of Workday, whose boards publish "Posted
    // Yesterday" as a phrase; those group under last_seen_at, which is when
    // the catalogue last confirmed them.
    const stamp = job.posted_at ?? job.last_seen_at;
    const parsed = Date.parse(stamp);
    const day = Number.isNaN(parsed)
      ? "unknown"
      : new Date(parsed).toISOString().slice(0, 10);
    by.set(day, [...(by.get(day) ?? []), job]);
  }
  return [...by.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([day, rows]) => ({ day, items: rows }));
}

export default function MatchesPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tJobs = useTranslations("jobs");
  const format = useFormatter();
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
        <div className="grid gap-5">
          <section className="rr-card px-5 py-4" data-testid="matches-weekly">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("picksHeading")}{" "}
                <span className="font-normal text-muted-foreground">
                  · {t("picksScoredAgainst")}
                </span>
              </h2>
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
            </div>
            {/* DigestCard owns the tailoring flow and the degraded-build
                notice; a second copy would drift from it. */}
            {dash.digest ? (
              <div className="mt-3">
                <DigestCard
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

          <section className="rr-card px-5 py-4" data-testid="matches-fresh">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("freshHeading")}{" "}
                <span className="font-normal text-muted-foreground">
                  · {t("freshFromIngest")}
                </span>
              </h2>
              <p className="text-xs text-muted-foreground">{t("freshScoringRhythm")}</p>
            </div>
            {days.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">{t("matchEmpty")}</p>
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
                      <li
                        key={job.id}
                        className="flex items-center gap-2.5 border-b border-rule py-2 last:border-b-0"
                      >
                        <EmployerLogo company={job.company} size="sm" />
                        <span className="min-w-0 flex-1">
                          <Link
                            href={`/jobs/${job.id}`}
                            className="block truncate text-[0.8125rem] font-medium"
                          >
                            {job.title}
                          </Link>
                          <span className="block truncate text-xs text-muted-foreground">
                            {job.company}
                            {job.location ? ` · ${job.location}` : ""}
                            {job.remote ? ` · ${tJobs("remote")}` : ""}
                          </span>
                        </span>
                        <span
                          className="w-8 shrink-0 text-right font-display text-[1.05rem] font-medium text-muted-foreground"
                          title={t("freshNotScored")}
                        >
                          —
                        </span>
                        <span className="w-[4.75rem] shrink-0 text-right">
                          <Link
                            href={`/jobs/${job.id}`}
                            className="rr-btn rr-btn-ghost px-2.5 py-1 text-xs"
                          >
                            {t("open")}
                          </Link>
                        </span>
                      </li>
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
