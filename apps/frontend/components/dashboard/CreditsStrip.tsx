"use client";

import { useEffect, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { fetchUsage, type Usage } from "@/lib/api";
import { fetchUsageBreakdown, type UsageBreakdown } from "@/lib/dashboard";
import { PURPOSE_KEY } from "./usagePurpose";

/**
 * One line of credits, between the next action and the matches.
 *
 * That position is the point. The next thing you do here is spend credits
 * tailoring, so the budget belongs in front of you *before* you pick which
 * role to spend it on. One line, so it informs the choice without becoming
 * the subject.
 *
 * Three things make it worth more than the meter already in the nav: the
 * tier-plus-grant split is the only place the ceiling is explained, the
 * largest spend category is the fact that changes behaviour, and the reset is
 * a countdown rather than a weekday, because "resets Monday" means something
 * different on a Saturday than on a Tuesday.
 *
 * Renders nothing when limits are not enforced, matching `UsageMeter`: an
 * empty meter means nothing, and in development it would be noise.
 */
export default function CreditsStrip() {
  const t = useTranslations("dashboard");
  const format = useFormatter();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [spend, setSpend] = useState<UsageBreakdown | null>(null);

  useEffect(() => {
    let live = true;
    fetchUsage()
      .then((u) => live && setUsage(u))
      .catch(() => {});
    // The breakdown is a nicety on this strip; a failure drops the one line
    // it feeds rather than the strip.
    fetchUsageBreakdown()
      .then((b) => live && setSpend(b))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  if (!usage || !usage.enforced) return null;

  const countCapped = usage.turn_limit !== null;
  const limit = countCapped ? usage.turn_limit : usage.limit_credits;
  if (limit === null || limit <= 0) return null;
  const used = countCapped ? usage.turns_used : usage.used_credits;
  const left = countCapped ? usage.turns_remaining : usage.remaining_credits;

  const tierShare = usage.tier_credits ?? 0;
  const grantShare = usage.granted_credits ?? 0;
  const pct = (part: number) => `${Math.max(0, Math.min(100, (part / limit) * 100))}%`;
  // Spent against each half, tier first: the grant is the part that expires,
  // so showing it as the one being eaten last is the honest order.
  const spentOfTier = Math.min(used, tierShare);
  const spentOfGrant = Math.max(0, used - tierShare);

  const reset = new Date(usage.reset_at);
  const resetValid = !Number.isNaN(reset.getTime());
  const top = spend?.slices[0];
  const low = left !== null && left / limit <= 0.2;

  return (
    <section
      className="rr-card flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3"
      data-testid="dashboard-credits"
      aria-labelledby="dash-credits"
    >
      <div className="shrink-0">
        <p
          id="dash-credits"
          className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-muted-foreground"
        >
          {t("creditsHeading")}
        </p>
        <p className="mt-0.5 font-display text-[1.35rem] font-medium leading-none tracking-[-0.03em]">
          <span className={`[font-variant-numeric:tabular-nums] ${low ? "text-warn" : ""}`}>
            {left ?? 0}
          </span>{" "}
          <span className="font-sans text-sm font-semibold text-muted-foreground">
            {t("creditsLeft", { limit })}
          </span>
        </p>
      </div>

      <div className="min-w-[9rem] flex-[1_1_14rem]">
        <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
          <span
            className={low ? "block bg-warn" : "block bg-primary"}
            style={{ width: pct(spentOfTier) }}
          />
          <span className="block bg-chart-4" style={{ width: pct(spentOfGrant) }} />
        </div>
        <p className="mt-1.5 flex flex-wrap gap-x-3.5 gap-y-0.5 text-[0.78rem] text-muted-foreground">
          <span>{t("creditsTier", { credits: tierShare })}</span>
          {grantShare > 0 ? <span>{t("creditsGranted", { credits: grantShare })}</span> : null}
          {top ? (
            <span>
              {t("creditsTopSpend", {
                label: t(
                  (PURPOSE_KEY[top.purpose] ?? "purposeOther") as "purposeChat",
                ),
              })}
            </span>
          ) : null}
          {resetValid ? (
            <span>
              {t("creditsResets", {
                date: format.relativeTime(reset, new Date()),
              })}
            </span>
          ) : null}
        </p>
      </div>

      <Link href="/dashboard/usage" className="rr-btn rr-btn-ghost shrink-0 px-3 py-1.5 text-xs">
        {t("usageDetails")}
      </Link>
    </section>
  );
}
