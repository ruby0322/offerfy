"use client";

import { useTranslations } from "next-intl";

/**
 * One pick drawn in the shape of a real one, so a new account can see what is
 * coming before it has anything.
 *
 * The rule it must obey: it cannot be mistaken for data about the reader. So
 * it is not built from MatchRow (no logo lookup, no link, no action), the
 * employer is the literal words "Example company" rather than a plausible
 * name, the whole block is dashed and greyed with no success colour on the
 * score, and an "Example" badge and a sentence both say what it is. Nothing in
 * it is a control: a greyed thing that looks clickable is a broken button.
 */
export default function SamplePick() {
  const t = useTranslations("dashboard");
  return (
    <div
      className="mt-2 rounded-md border border-dashed border-rule bg-muted/30 px-3 py-2.5 opacity-70"
      role="group"
      aria-label={t("sampleBadge")}
      data-testid="dashboard-sample-pick"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded-full border border-rule px-2 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
          {t("sampleBadge")}
        </span>
        <span className="text-xs text-muted-foreground">{t("sampleNote")}</span>
      </div>
      <div className="mt-2 flex items-start gap-2.5">
        <span className="min-w-0 flex-1">
          <span className="block break-words text-[0.8125rem] font-medium">
            {t("sampleTitle")}
          </span>
          <span className="block break-words text-xs text-muted-foreground">
            {t("sampleCompany")}
          </span>
          <span className="mt-1 block break-words text-[0.8125rem]">{t("sampleReason")}</span>
        </span>
        <span className="min-w-8 shrink-0 text-right">
          <span className="block font-display text-[1.05rem] font-medium leading-none text-muted-foreground [font-variant-numeric:tabular-nums]">
            {t("sampleScore")}
          </span>
          <span className="mt-0.5 block text-[0.625rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            {t("digestTierStrongFit")}
          </span>
        </span>
      </div>
    </div>
  );
}
