"use client";

import { adminUser, type AdminUserDetail } from "@/lib/api";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { use, useEffect, useState } from "react";

function tierKey(tier: string) {
  if (tier === "guest") return "usageTier_guest" as const;
  if (tier === "matching") return "usageTier_matching" as const;
  return "usageTier_registered" as const;
}

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminUserDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const t = useTranslations("admin");
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminUser(id)
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{data.email}</h1>
      <p className="text-sm text-muted-foreground">
        {t("locale")} {data.locale}
        {" · "}
        {t("googleSub")} {data.google_sub}
        {" · "}
        {t("created")} {stamp(data.created_at)}
      </p>
      {/* This week against their tier, plus all time, so it is visible whether
          this week is typical for them. */}
      <section className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-md border border-border p-3">
          <p className="text-xs uppercase text-muted-foreground">{t("usageColTier")}</p>
          <p className="text-base font-semibold">{t(tierKey(data.usage.tier))}</p>
        </div>
        <div className="rounded-md border border-border p-3">
          <p className="text-xs uppercase text-muted-foreground">{t("usageThisWeek")}</p>
          <p className="text-base font-semibold">
            {data.usage.credits_used}
            {data.usage.limit_credits !== null ? (
              <span className="text-xs font-normal text-muted-foreground">
                {" "}/ {data.usage.limit_credits}
              </span>
            ) : null}
          </p>
        </div>
        <div className="rounded-md border border-border p-3">
          <p className="text-xs uppercase text-muted-foreground">{t("usageColTurns")}</p>
          <p className="text-base font-semibold">
            {data.usage.turns_used}
            {data.usage.turn_limit !== null ? (
              <span className="text-xs font-normal text-muted-foreground">
                {" "}/ {data.usage.turn_limit}
              </span>
            ) : null}
          </p>
        </div>
        <div className="rounded-md border border-border p-3">
          <p className="text-xs uppercase text-muted-foreground">{t("usageLifetime")}</p>
          <p className="text-base font-semibold tabular-nums">
            ${(data.usage.lifetime_uusd / 1_000_000).toFixed(4)}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("usageLifetimeTurns", { count: data.usage.lifetime_turns })}
          </p>
        </div>
      </section>
      <p className="text-xs text-muted-foreground">
        {t("usageResetsAt", { date: stamp(data.usage.reset_at) })}
        {data.usage.refusals > 0 ? ` · ${t("usageRefusedTimes", { count: data.usage.refusals })}` : ""}
        {data.usage.unmeasured_rounds > 0
          ? ` · ${t("usageUnmeasuredRounds", { count: data.usage.unmeasured_rounds })}`
          : ""}
      </p>
      {data.resumes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-2 font-medium">{t("title")}</th>
              <th className="py-2 font-medium">{t("source")}</th>
              <th className="py-2 font-medium">{t("importStatus")}</th>
              <th className="py-2 font-medium">{t("messages")}</th>
              <th className="py-2 font-medium">{t("created")}</th>
            </tr>
          </thead>
          <tbody>
            {data.resumes.map((row) => (
              <tr key={row.id} className="border-b border-border/70">
                <td className="py-2">
                  <Link href={`/admin/resumes/${row.id}`} className="underline">
                    {row.title}
                  </Link>
                </td>
                <td className="py-2">{row.source}</td>
                <td className="py-2">{row.import_status}</td>
                <td className="py-2">{row.message_count}</td>
                <td className="py-2">{stamp(row.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
