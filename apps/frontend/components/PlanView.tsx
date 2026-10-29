"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import ModelCostTable from "@/components/ModelCostTable";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import UsageMeter from "@/components/UsageMeter";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Link, useRouter } from "@/i18n/navigation";
import {
  fetchModels,
  fetchUsage,
  fetchUsageTierTable,
  getMe,
  type ModelTable,
  type Usage,
  type UsageGrant,
  type UsageTier,
  type UsageTierRow,
  type UsageTiers,
} from "@/lib/api";

/**
 * One person's plan and what is left of this week's allowance.
 *
 * The division of labour with the three pages that already touch this:
 *
 *   /usage    — the public policy. What the limits are and why they exist.
 *   /pricing  — the same table in the shape of a pricing page.
 *   /settings — the controls, with a summary card beside the matching toggle.
 *   here      — your numbers. Which plan you are on, what it grants, how much
 *               of it is spent, when it resets, and what the other plans give.
 *
 * It holds no copy of the allowances. Both the current balance and the tier
 * table are fetched, for the reason lib/usage-tiers.ts gives: a published
 * figure that has drifted from the enforced one is the worst bug this page
 * could have, and fetching makes the drift impossible rather than merely
 * tested for.
 *
 * The plan is derived from the usage endpoint's `tier`, not from the consent
 * endpoint. Both would answer, but the tier is what the quota is actually
 * enforced against — reading the consent separately would let this page show a
 * plan the meter below it disagrees with.
 */

function nameKey(tier: UsageTier) {
  if (tier === "guest") return "tier_guest" as const;
  if (tier === "matching") return "tier_matching" as const;
  return "tier_registered" as const;
}

function reasonKey(reason: string) {
  switch (reason) {
    case "compensation":
      return "grantReason_compensation" as const;
    case "beta":
      return "grantReason_beta" as const;
    case "staff":
      return "grantReason_staff" as const;
    case "promotion":
      return "grantReason_promotion" as const;
    // Including 'manual', which is an operator's category and means nothing to
    // the person who received it. They get the generic noun instead.
    default:
      return "grantReason_extra" as const;
  }
}

function grantStatusKey(grant: UsageGrant) {
  switch (grant.status) {
    case "scheduled":
      return "grantStatus_scheduled" as const;
    case "expired":
      return "grantStatus_expired" as const;
    case "revoked":
      return "grantStatus_revoked" as const;
    default:
      return "grantStatus_active" as const;
  }
}

function blurbKey(tier: UsageTier) {
  if (tier === "guest") return "blurb_guest" as const;
  if (tier === "matching") return "blurb_matching" as const;
  return "blurb_registered" as const;
}

export default function PlanView() {
  const t = useTranslations("plan");
  const tCommon = useTranslations("common");
  const tModels = useTranslations("models");
  const locale = useLocale();
  const router = useRouter();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [tiers, setTiers] = useState<UsageTiers | null>(null);
  const [models, setModels] = useState<ModelTable | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!me) {
          router.replace("/login");
          return;
        }
        // In parallel, and the table is allowed to fail on its own: the
        // balance is why someone opened this page, and losing the comparison
        // should not cost them that.
        const [balance, table, catalogue] = await Promise.all([
          fetchUsage(),
          fetchUsageTierTable().catch(() => null),
          fetchModels().catch(() => null),
        ]);
        if (cancelled) return;
        setUsage(balance);
        setTiers(table);
        setModels(catalogue);
      } catch {
        if (!cancelled) setError(t("loadFailed"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  // The app's locale, not the browser's: ChatMessageCard already formats
  // times this way, and without it a /zh-TW page prints "Oct 27" because the
  // headless browser -- or anyone whose system is in English -- said so.
  const dateFmt = (iso: string) =>
    new Date(iso).toLocaleDateString(locale, { month: "short", day: "numeric" });

  function allowance(row: UsageTierRow): string {
    return row.turns !== null
      ? t("allowanceEdits", { count: row.turns })
      : t("allowanceCredits", { credits: row.credits ?? 0, edits: row.approx_edits });
  }

  // Exactly one side of a tier is capped: a guest on a count of turns, an
  // account on cost. Read whichever is set, the same way UsageMeter does.
  const countCapped = usage ? usage.turn_limit !== null : false;
  const used = usage ? (countCapped ? usage.turns_used : usage.used_credits) : 0;
  const limit = usage ? (countCapped ? usage.turn_limit : usage.limit_credits) : null;
  const remaining = usage
    ? countCapped
      ? usage.turns_remaining
      : usage.remaining_credits
    : null;
  const editsLeft = usage?.approx_edits_left ?? null;
  const current = usage ? (tiers?.tiers.find((row) => row.name === usage.tier) ?? null) : null;

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-2xl px-5 py-12">
        <h1 className="rr-page-title mb-2">{t("title")}</h1>
        <p className="mb-8 text-sm text-muted-foreground">{t("lede")}</p>

        {error ? <p className="mb-4 text-sm text-destructive">{error}</p> : null}
        {!usage && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}

        {usage ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2">
                  {t(nameKey(usage.tier))}
                  {/* The price, stated rather than implied. There is no paid
                      plan to compare against, so a plan page that showed only
                      an allowance would leave the obvious question hanging. */}
                  <Badge variant="secondary">{t("free")}</Badge>
                </CardTitle>
                <CardDescription>{t(blurbKey(usage.tier))}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {/* The grant, not the remainder -- that is the card below.
                    Taken from the tier row when the table loaded, so the
                    "about N edits" here is the published blend and reads the
                    same as the row for this plan further down. Without the
                    table, the ceiling alone: a figure derived from this
                    person's own balance would be a different number wearing
                    the same words. */}
                <p className="text-sm">
                  {current
                    ? allowance(current)
                    : countCapped
                      ? t("allowanceEdits", { count: usage.turn_limit ?? 0 })
                      : t("grantCredits", { credits: usage.tier_credits ?? 0 })}
                </p>
                {/* Without this the line above -- the *published* allowance
                    for this plan -- contradicts the meter below it, which
                    counts against the granted ceiling. Both numbers are true;
                    the page has to say which is which. */}
                {usage.granted_credits > 0 && usage.tier_credits !== null ? (
                  <p className="text-sm">
                    {t("grantedLine", {
                      granted: usage.granted_credits,
                      total: usage.tier_credits + usage.granted_credits,
                    })}
                  </p>
                ) : null}
                {/* Matching is a plan, so its state belongs here rather than
                    only on /settings -- but the control stays there. Saying it
                    in both places and switching it in one keeps the consent
                    decision where its full notice is. */}
                <p className="text-xs leading-5 text-muted-foreground">
                  {usage.tier === "matching" ? t("matchingOn") : t("matchingOff")}
                </p>
                <p className="text-xs">
                  <Link href="/settings" className="underline underline-offset-2">
                    {usage.tier === "matching" ? t("manageMatching") : t("turnOnMatching")}
                  </Link>
                </p>
              </CardContent>
            </Card>

            <Card className="mt-6">
              <CardHeader>
                <CardTitle>{t("thisWeekTitle")}</CardTitle>
                <CardDescription>
                  {t("period", {
                    start: dateFmt(usage.period_start),
                    end: dateFmt(usage.reset_at),
                  })}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {usage.enforced ? (
                  <>
                    <UsageMeter
                      usage={usage}
                      label={
                        countCapped
                          ? t("meterEdits", { used, limit: limit ?? 0 })
                          : t("meterCredits", { used, limit: limit ?? 0 })
                      }
                      // Dropped on a turn-capped tier: "3 of 8 AI edits" is
                      // already the edit count, so the estimate would repeat
                      // it. And at zero, "about 0 more edits" says worse what
                      // a full bar has already said.
                      editsLeftLabel={
                        !countCapped && editsLeft !== null && editsLeft > 0
                          ? t("editsLeft", { count: editsLeft })
                          : null
                      }
                      resetLabel={t("resetsOn", { date: dateFmt(usage.reset_at) })}
                    />
                    <dl className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <dt className="text-xs text-muted-foreground">{t("usedLabel")}</dt>
                        <dd className="font-medium">{used}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">{t("remainingLabel")}</dt>
                        {/* max(0): the last turn of a week is always allowed
                            to finish, so the cost side can legitimately go
                            negative. That overrun is carried as debt rather
                            than clawed back -- but "-1.4 remaining" reads as a
                            bug, and the full bar above already says it. */}
                        <dd className="font-medium">{Math.max(remaining ?? 0, 0)}</dd>
                      </div>
                    </dl>
                    {usage.exhausted ? (
                      <p className="text-sm">{t("exhausted")}</p>
                    ) : null}
                    {/* A meter that is guessing should say so, rather than
                        presenting a fallback estimate as a measurement. */}
                    {usage.unmeasured_rounds > 0 ? (
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t("unmeasured", { count: usage.unmeasured_rounds })}
                      </p>
                    ) : null}
                  </>
                ) : (
                  /* Development and staging. An empty meter here would mean
                     nothing, and a full one would be a lie. */
                  <p className="text-sm text-muted-foreground">{t("notEnforced")}</p>
                )}
              </CardContent>
            </Card>

            {usage.grants.length > 0 ? (
              <Card className="mt-6">
                <CardHeader>
                  <CardTitle>{t("grantsTitle")}</CardTitle>
                  <CardDescription>{t("grantsNotice")}</CardDescription>
                </CardHeader>
                <CardContent>
                  <ul className="flex flex-col divide-y divide-rule">
                    {usage.grants.map((row, i) => (
                      <li
                        key={`${row.starts_at}-${i}`}
                        className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-3 first:pt-0 last:pb-0"
                      >
                        <span className="flex items-center gap-2 text-sm font-medium">
                          {t("grantAmount", { credits: row.credits })}
                          <Badge
                            variant={row.status === "active" ? "secondary" : "outline"}
                          >
                            {t(grantStatusKey(row))}
                          </Badge>
                        </span>
                        <span className="text-sm text-muted-foreground">
                          {t(reasonKey(row.reason))}
                          {" · "}
                          {row.expires_at
                            ? t("grantPeriod", {
                                start: dateFmt(row.starts_at),
                                end: dateFmt(row.expires_at),
                              })
                            : t("grantFrom", { start: dateFmt(row.starts_at) })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ) : null}

            {tiers ? (
              <Card className="mt-6">
                <CardHeader>
                  <CardTitle>{t("allPlansTitle")}</CardTitle>
                  <CardDescription>{t("allPlansNotice")}</CardDescription>
                </CardHeader>
                <CardContent>
                  <ul className="flex flex-col divide-y divide-rule">
                    {tiers.tiers.map((row) => (
                      <li
                        key={row.name}
                        className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-3 first:pt-0 last:pb-0"
                      >
                        {/* span, not p: Badge renders a div, and a div inside
                            a p is a hydration error rather than a style bug. */}
                        <span className="flex items-center gap-2 text-sm font-medium">
                          {t(nameKey(row.name))}
                          {row.name === usage.tier ? (
                            <Badge variant="outline">{t("yourPlan")}</Badge>
                          ) : null}
                        </span>
                        <span className="text-sm text-muted-foreground">{allowance(row)}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ) : null}

            {models ? (
              <Card className="mt-6">
                <CardHeader>
                  <CardTitle>{tModels("heading")}</CardTitle>
                  <CardDescription>{tModels("intro")}</CardDescription>
                </CardHeader>
                <CardContent>
                  {/* The one page with a reader to be specific about, so this
                      is the only place the table marks a row as theirs and
                      quotes the figures against their own tier. The control
                      itself stays in the editor's composer, where the message
                      the model will answer is actually written. */}
                  <ModelCostTable
                    table={models}
                    tier={usage.tier}
                    selected={usage.model}
                  />
                </CardContent>
              </Card>
            ) : null}

            <p className="mt-6 flex flex-wrap gap-4 text-xs">
              <Link href="/usage" className="underline underline-offset-2">
                {t("howItWorks")}
              </Link>
              <Link href="/pricing" className="underline underline-offset-2">
                {t("seePricing")}
              </Link>
            </p>
          </>
        ) : null}
      </main>
      <Footer variant="app" />
    </div>
  );
}
