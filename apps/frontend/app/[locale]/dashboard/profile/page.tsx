"use client";

import { useEffect, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, getMe, listResumes, logout } from "@/lib/api";
import {
  fetchDashboard,
  fetchDisclosures,
  type Dashboard,
  type DisclosureRecipient,
} from "@/lib/dashboard";
import { preferenceSummary } from "@/lib/jobs";

/**
 * One page for the two things matching actually runs on, and the record of
 * who has been shown the result.
 *
 * Matching reads exactly two inputs: what you say you want, and the published
 * resume it was allowed to read. Those lived on /preferences and inside a
 * resume-health card on the old dashboard, so nobody could see them as one
 * thing. Here they are stacked, with the number that makes them concrete in
 * the first card -- change a filter and watch the match count move.
 *
 * `/preferences` and `/settings` remain the places you edit; this frames them
 * rather than becoming a third copy of the same forms.
 */
export default function ProfilePage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tJobs = useTranslations("jobs");
  const format = useFormatter();
  const router = useRouter();
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [masterTitle, setMasterTitle] = useState<string | null>(null);
  const [seen, setSeen] = useState<DisclosureRecipient[] | null>(null);
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
    getMe()
      .then((me) => !cancelled && setEmail(me?.email ?? null))
      .catch(() => {});
    listResumes()
      .then((items) => {
        if (cancelled) return;
        const master = items.find((r) => (r.kind ?? "master") === "master");
        setMasterTitle(master?.title ?? null);
      })
      .catch(() => {});
    // The named list is the point of the employer-views number; a failure
    // drops the table, not the page.
    fetchDisclosures()
      // Array.isArray, not a cast: this panel is the useful half of the
      // employer-views number, but an unexpected shape should cost the table
      // rather than the page.
      .then((rows) => !cancelled && setSeen(Array.isArray(rows) ? rows : []))
      .catch(() => !cancelled && setSeen([]));
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

  const health = dash?.resume_health;
  const checks = health
    ? [
        { ok: health.has_master, key: "checkMaster" },
        { ok: summary !== "", key: "checkPreference" },
        { ok: health.facts_state === "fresh", key: "checkFacts" },
        { ok: !health.needs_rebuild, key: "checkCurrent" },
      ]
    : [];
  const ready = checks.filter((c) => c.ok).length;

  async function onLogout() {
    try {
      await logout();
    } finally {
      router.push("/");
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
      <div className="mb-6">
        <h1 className="rr-page-title">{t("profileTitle")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("profileLead")}</p>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {!dash && !error ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : null}

      {dash && health ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="grid gap-4">
            <section className="rr-card px-5 py-4" data-testid="profile-preference">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                  {t("wantHeading")}
                </h2>
                <Link href="/preferences" className="text-xs underline text-primary">
                  {t("editLink")}
                </Link>
              </div>
              <dl className="mt-2.5">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-rule py-2 text-sm">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("wantSummary")}</dt>
                  <dd className="min-w-0 flex-1">{summary || t("matchEmpty")}</dd>
                </div>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 text-sm">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("wantMatching")}</dt>
                  <dd className="min-w-0 flex-1">
                    <span className="font-semibold [font-variant-numeric:tabular-nums]">
                      {dash.match_count}
                    </span>{" "}
                    {t("wantMatchingRoles")}
                  </dd>
                  <Link href="/dashboard/matches" className="text-xs underline text-primary">
                    {t("wantSeeThem")}
                  </Link>
                </div>
              </dl>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">{t("wantNote")}</p>
            </section>

            <section className="rr-card px-5 py-4" data-testid="profile-resume">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                  {t("matchResumeHeading")}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {t("checksReady", { ready, total: checks.length })}
                </p>
              </div>
              <dl className="mt-2.5">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-rule py-2 text-sm">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("matchResumeSource")}</dt>
                  <dd className="min-w-0 flex-1">
                    {masterTitle ?? (health.has_master ? t("metricNone") : t("healthEmpty"))}
                  </dd>
                  <Link href="/dashboard/resumes" className="text-xs underline text-primary">
                    {t("editLink")}
                  </Link>
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 py-2 text-sm">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("matchResumeRead")}</dt>
                  <dd className="min-w-0 flex-1">
                    {health.facts_state === "missing"
                      ? t("healthFactsMissing")
                      : health.facts_state === "stale"
                        ? t("healthFactsStale")
                        : health.needs_rebuild
                          ? t("healthRebuildNeeded")
                          : t("healthCurrent")}
                  </dd>
                </div>
              </dl>
              <ul className="mt-3 grid gap-1.5 border-t border-rule pt-3">
                {checks.map((check) => (
                  <li key={check.key} className="flex items-start gap-2 text-sm">
                    <span
                      className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full text-[0.6rem] font-bold ${
                        check.ok ? "bg-success text-sheet" : "bg-warn text-sheet"
                      }`}
                      aria-hidden="true"
                    >
                      {check.ok ? "✓" : "!"}
                    </span>
                    <span className={check.ok ? "" : "font-medium"}>
                      {t(check.key as "checkMaster")}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <div className="grid gap-4">
            <section className="rr-card px-5 py-4" data-testid="profile-seen">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                  {t("seenHeading")}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {t("employerViewsCount", { count: dash.employer_views.disclosure_count })}
                </p>
              </div>
              {seen === null ? (
                <p className="mt-2 text-sm text-muted-foreground">{tCommon("loading")}</p>
              ) : seen.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">{t("employerViewsEmpty")}</p>
              ) : (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-rule text-left">
                        {(["colEmployer", "colFirstSeen", "colLastSeen"] as const).map((key) => (
                          <th
                            key={key}
                            className="whitespace-nowrap pb-2 pr-3 text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground"
                          >
                            {t(key)}
                          </th>
                        ))}
                        <th className="pb-2 text-right text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
                          {t("colTimes")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {seen.map((row) => (
                        <tr
                          key={`${row.recipient_label}-${row.first_disclosed_at}`}
                          className="border-b border-rule last:border-b-0"
                        >
                          <td className="py-2 pr-3 align-middle">
                            <span className="font-medium">
                              {row.recipient_label ?? t("seenUnnamed")}
                            </span>
                            {row.recipient_contact ? (
                              <span className="block truncate text-xs text-muted-foreground">
                                {row.recipient_contact}
                              </span>
                            ) : null}
                          </td>
                          <td className="whitespace-nowrap py-2 pr-3 align-middle text-xs text-muted-foreground">
                            {format.dateTime(new Date(row.first_disclosed_at), {
                              month: "short",
                              day: "numeric",
                            })}
                          </td>
                          <td className="whitespace-nowrap py-2 pr-3 align-middle text-xs text-muted-foreground">
                            {format.dateTime(new Date(row.last_disclosed_at), {
                              month: "short",
                              day: "numeric",
                            })}
                          </td>
                          <td className="py-2 text-right align-middle text-xs text-muted-foreground [font-variant-numeric:tabular-nums]">
                            {row.disclosure_count}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {/* The ledger keeps disclosures after consent is withdrawn, on
                  purpose: someone who re-grants still needs to know who holds
                  the old copy. */}
              <p className="mt-2.5 text-xs leading-5 text-muted-foreground">{t("seenNote")}</p>
              <Link
                href="/settings"
                className="mt-2 inline-block text-xs underline text-primary"
              >
                {t("seenManageConsent")}
              </Link>
            </section>

            <section className="rr-card px-5 py-4" data-testid="profile-account">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("accountHeading")}
              </h2>
              <dl className="mt-2.5">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-rule py-2 text-sm">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("accountSignedIn")}</dt>
                  <dd className="min-w-0 flex-1 truncate">{email ?? t("metricNone")}</dd>
                </div>
              </dl>
              <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-rule pt-3">
                <Link href="/settings" className="text-xs underline text-muted-foreground">
                  {t("accountSettings")}
                </Link>
                <button
                  type="button"
                  className="text-xs underline text-muted-foreground"
                  onClick={() => void onLogout()}
                >
                  {t("logout")}
                </button>
              </div>
            </section>
          </div>
        </div>
      ) : null}
    </main>
  );
}
