"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Link } from "@/i18n/navigation";
import { fetchUsage, type Usage } from "@/lib/api";

/**
 * This week's AI allowance, on the settings page.
 *
 * Placed near the matching toggle but deliberately **not inside it**. The
 * toggle's copy leads with what matching is and mentions the quota second;
 * nesting a credit meter inside the consent control would invert that and make
 * the bonus read as the reason to consent.
 */
export default function UsageCard() {
  const t = useTranslations("settings");
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    fetchUsage()
      .then((next) => {
        if (live) setUsage(next);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, []);

  if (failed || !usage) return null;

  const countCapped = usage.turn_limit !== null;
  const used = countCapped ? usage.turns_used : usage.used_credits;
  const limit = countCapped ? usage.turn_limit : usage.limit_credits;
  const tierName = t(`usageTier_${usage.tier}`);
  const reset = new Date(usage.reset_at);

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>{t("usageTitle")}</CardTitle>
        <CardDescription>{t("usageNotice")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm">
          {countCapped
            ? t("usageThisWeekEdits", { used, limit: limit ?? 0 })
            : t("usageThisWeekCredits", { used, limit: limit ?? 0 })}
        </p>
        <p className="text-xs leading-5 text-muted-foreground">
          {t("usageTierLine", { tier: tierName })}
          {usage.enforced ? ` ${t("usageResetLine", { date: reset.toLocaleDateString() })}` : ""}
        </p>
        {/* Says so when the meter is guessing, rather than presenting a
            fallback estimate as a measurement. */}
        {usage.unmeasured_rounds > 0 ? (
          <p className="text-xs leading-5 text-muted-foreground">
            {t("usageUnmeasured", { count: usage.unmeasured_rounds })}
          </p>
        ) : null}
        <p className="text-xs">
          <Link href="/usage" className="underline underline-offset-2">
            {t("usageLearnMore")}
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
