"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";

type Props = {
  count: number;
  /** False until the person says what they want; then there is no count to show. */
  hasPreference: boolean;
};

export default function MatchCountCard({ count, hasPreference }: Props) {
  const t = useTranslations("dashboard");
  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-matches" aria-labelledby="dash-matches">
      <h2 id="dash-matches" className="text-sm font-semibold tracking-tight">
        {t("headingMatches")}
      </h2>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm">
        <p>
          {hasPreference ? (
            <span className="font-medium">{t("matchCount", { count })}</span>
          ) : (
            <span className="text-muted-foreground">{t("matchEmpty")}</span>
          )}
        </p>
        <Link href="/jobs" className="underline">
          {t("browseRoles")}
        </Link>
      </div>
    </section>
  );
}
