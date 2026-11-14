"use client";

import { useEffect, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { purposeColor, purposeKey } from "@/components/dashboard/usagePurpose";
import { fetchUsage, type Usage } from "@/lib/api";
import { fetchUsageBreakdown, type UsageBreakdown } from "@/lib/dashboard";

/**
 * Where the week's allowance went.
 *
 * "You have 14.2 credits left" is not actionable. "58% of it went on editing
 * chat and 17% on one import" is, which is why this page exists and why the
 * breakdown is its centre rather than the meter.
 *
 * One thing it deliberately does not do: estimate how many edits are left.
 * That figure divides the remaining balance by the person's own trailing cost
 * per turn, and it goes badly wrong the moment they switch models -- measured
 * once at 43 edits where the published figure for the model they had picked
 * was 793. `UsageMeter` records that decision; this page keeps it, and shows
 * published per-model rates instead so the arithmetic is the reader's but
 * every number in it is true.
 */
export default function UsagePage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tSettings = useTranslations("settings");
  const format = useFormatter();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [spend, setSpend] = useState<UsageBreakdown | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    fetchUsage()
      .then((u) => live && setUsage(u))
      .catch(() => live && setFailed(true));
    fetchUsageBreakdown()
      .then((b) => live && setSpend(b))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const total = spend?.total_credits ?? 0;
  const slices = spend?.slices ?? [];
  const dayMax = useMemo(
    () => Math.max(0, ...(spend?.daily ?? []).map(([, credits]) => credits)),
    [spend],
  );

  if (failed) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
        <h1 className="rr-page-title">{t("usageTitle")}</h1>
        <p className="mt-3 text-sm text-destructive">{t("needLogin")}</p>
      </main>
    );
  }

  const countCapped = usage?.turn_limit !== null && usage?.turn_limit !== undefined;
  const limit = usage ? (countCapped ? usage.turn_limit : usage.limit_credits) : null;
  const used = usage ? (countCapped ? usage.turns_used : usage.used_credits) : 0;
  const left = usage ? (countCapped ? usage.turns_remaining : usage.remaining_credits) : null;
  const tierShare = usage?.tier_credits ?? 0;
  const grantShare = usage?.granted_credits ?? 0;
  const reset = usage ? new Date(usage.reset_at) : null;
  const resetValid = reset !== null && !Number.isNaN(reset.getTime());

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="rr-page-title">{t("usageTitle")}</h1>
          {resetValid ? (
            <p className="mt-2 text-sm text-muted-foreground">
              {t("usageResetsIn", {
                when: format.relativeTime(reset, new Date()),
              })}
            </p>
          ) : null}
        </div>
        <Link href="/plan" className="rr-btn rr-btn-ghost">
          {t("comparePlans")}
        </Link>
      </div>

      {!usage ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="grid gap-4">
            <section className="rr-card px-5 py-4" data-testid="usage-allowance">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                  {t("allowanceHeading")}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {tSettings(`usageTier_${usage.tier}` as "usageTier_guest")}
                </p>
              </div>
              <p className="mt-3 font-display text-[2rem] font-medium leading-none tracking-[-0.03em] [font-variant-numeric:tabular-nums]">
                {used}
                <span className="ml-1 font-sans text-sm font-semibold text-muted-foreground">
                  {countCapped
                    ? t("allowanceOfEdits", { limit: limit ?? 0 })
                    : t("allowanceOfCredits", { limit: limit ?? 0 })}
                </span>
              </p>
              {/* The ceiling is a tier plus grants, and the number 70 has no
                  explanation anywhere else in the product. */}
              {limit && limit > 0 ? (
                <>
                  <div className="mt-3 flex h-2.5 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block bg-primary"
                      style={{ width: `${(Math.min(used, tierShare) / limit) * 100}%` }}
                    />
                    <span
                      className="block bg-chart-4"
                      style={{
                        width: `${(Math.max(0, used - tierShare) / limit) * 100}%`,
                      }}
                    />
                  </div>
                  <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.8125rem] text-muted-foreground">
                    <span>
                      <span
                        className="mr-1.5 inline-block size-2 rounded-sm bg-primary align-[-1px]"
                        aria-hidden="true"
                      />
                      {t("creditsTier", { credits: tierShare })}
                    </span>
                    {grantShare > 0 ? (
                      <span>
                        <span
                          className="mr-1.5 inline-block size-2 rounded-sm bg-chart-4 align-[-1px]"
                          aria-hidden="true"
                        />
                        {t("allowanceGrantedFor", { credits: grantShare })}
                      </span>
                    ) : null}
                    <span>{t("allowanceLeft", { left: left ?? 0 })}</span>
                  </p>
                </>
              ) : null}
              {usage.grants.length > 0 ? (
                <ul className="mt-3 grid gap-1 border-t border-rule pt-3 text-xs text-muted-foreground">
                  {usage.grants.map((grant, i) => (
                    <li key={`${grant.reason}-${i}`}>
                      {t("grantLine", {
                        credits: grant.credits,
                        reason: grant.reason,
                        status: grant.status,
                      })}
                      {grant.expires_at
                        ? ` · ${t("grantExpires", {
                            date: format.dateTime(new Date(grant.expires_at), {
                              dateStyle: "medium",
                            }),
                          })}`
                        : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              {/* Says so when the meter is estimating, rather than presenting
                  a fallback rate as a measurement. */}
              {usage.unmeasured_rounds > 0 ? (
                <p className="mt-3 rounded-md border border-dashed border-rule px-3 py-2 text-xs leading-5 text-muted-foreground">
                  {tSettings("usageUnmeasured", { count: usage.unmeasured_rounds })}
                </p>
              ) : null}
            </section>

            <section className="rr-card px-5 py-4" data-testid="usage-breakdown">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                  {t("whereHeading")}
                </h2>
                <p className="text-xs text-muted-foreground">{t("whereThisWindow")}</p>
              </div>
              {slices.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">{t("whereEmpty")}</p>
              ) : (
                <>
                  <div
                    className="mt-3 flex h-3.5 overflow-hidden rounded bg-muted"
                    aria-hidden="true"
                  >
                    {slices.map((slice) => (
                      <span
                        key={slice.purpose}
                        className="block"
                        style={{
                          width: `${total > 0 ? (slice.credits / total) * 100 : 0}%`,
                          background: purposeColor(slice.purpose),
                        }}
                      />
                    ))}
                  </div>
                  <ul className="mt-2.5 grid gap-1.5">
                    {slices.map((slice) => (
                      <li
                        key={slice.purpose}
                        className="flex items-baseline gap-2 text-[0.8125rem]"
                      >
                        <span
                          className="size-2 shrink-0 rounded-sm"
                          style={{ background: purposeColor(slice.purpose) }}
                          aria-hidden="true"
                        />
                        <span className="min-w-0 flex-1">
                          {t(purposeKey(slice.purpose) as "purposeChat")}
                        </span>
                        <span className="font-semibold [font-variant-numeric:tabular-nums]">
                          {slice.credits}
                        </span>
                        <span className="w-10 text-right text-muted-foreground [font-variant-numeric:tabular-nums]">
                          {total > 0 ? `${Math.round((slice.credits / total) * 100)}%` : "—"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {spend && spend.daily?.length ? (
                <div className="mt-4 border-t border-rule pt-3">
                  <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
                    {t("whereByDay")}
                  </p>
                  <div className="mt-2 flex h-14 items-end gap-1.5" aria-hidden="true">
                    {spend.daily.map(([day, credits]) => (
                      <span
                        key={day}
                        className="flex-1 rounded-t bg-primary/40"
                        style={{
                          height: `${dayMax > 0 ? Math.max(3, (credits / dayMax) * 100) : 3}%`,
                        }}
                      />
                    ))}
                  </div>
                  <div className="mt-1 flex gap-1.5 text-[0.6875rem] text-muted-foreground">
                    {spend.daily.map(([day]) => (
                      <span key={day} className="flex-1 text-center">
                        {format.dateTime(new Date(day), { weekday: "short" })}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </section>
          </div>

          <div className="grid gap-4">
            <section className="rr-card px-5 py-4" data-testid="usage-rates">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("ratesHeading")}
              </h2>
              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                {t("ratesNote")}
              </p>
              <dl className="mt-3">
                <div className="flex items-baseline justify-between gap-3 border-b border-rule py-2 text-sm">
                  <dt className="text-muted-foreground">{t("ratesInUse")}</dt>
                  <dd className="font-medium">{usage.model || t("metricNone")}</dd>
                </div>
              </dl>
              <Link
                href="/plan"
                className="mt-3 inline-block text-xs underline text-primary"
              >
                {t("ratesSeeTable")}
              </Link>
            </section>

            <section className="rr-card px-5 py-4" data-testid="usage-tiers">
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">
                {t("tiersHeading")}
              </h2>
              <ul className="mt-2.5 grid gap-1.5">
                {(["guest", "registered", "matching"] as const).map((tier) => (
                  <li
                    key={tier}
                    className={`flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm ${
                      usage.tier === tier ? "border-primary bg-muted" : "border-rule"
                    }`}
                  >
                    <span className="font-semibold">
                      {tSettings(`usageTier_${tier}` as "usageTier_guest")}
                    </span>
                    {usage.tier === tier ? (
                      <span className="text-[0.68rem] font-bold uppercase tracking-[0.06em] text-primary">
                        {t("tiersYou")}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p className="mt-2.5 text-xs leading-5 text-muted-foreground">
                {t("tiersNote")}
              </p>
            </section>
          </div>
        </div>
      )}
    </main>
  );
}
