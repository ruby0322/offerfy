"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { adminUsage, type AdminUsageOverview } from "@/lib/api";

/** Micro-USD to a readable dollar figure. 4 decimals: a round is ~$0.017. */
function usd(uusd: number): string {
  return `$${(uusd / 1_000_000).toFixed(4)}`;
}

function pct(share: number | null): string {
  return share === null ? "—" : `${Math.round(share * 100)}%`;
}

/**
 * next-intl types its message keys, so `t(`usageTier_${x}`)` is not a key.
 * These narrow a runtime string to the literal union the catalogue has, which
 * also means a tier the backend grows without copy here fails the build rather
 * than rendering a raw key.
 */
function tierKey(tier: string) {
  if (tier === "guest") return "usageTier_guest" as const;
  if (tier === "matching") return "usageTier_matching" as const;
  return "usageTier_registered" as const;
}

function kindKey(kind: string) {
  return kind === "network" ? ("usageKind_network" as const) : ("usageKind_guest" as const);
}

function ceilingKey(name: string) {
  return name === "guest_pool"
    ? ("usageCeiling_guest_pool" as const)
    : ("usageCeiling_global" as const);
}

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 16);
}

/**
 * What AI editing is costing, and who is costing it.
 *
 * Read off usage_counters rather than aggregated from llm_calls, because the
 * counters are what the limit is enforced against -- a page built on the
 * telemetry would show numbers nobody was actually held to.
 */
export default function AdminUsagePage() {
  const t = useTranslations("admin");
  const [data, setData] = useState<AdminUsageOverview | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminUsage({ top: 50 })
      .then((next) => !cancelled && setData(next))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-destructive">{t("loadError")}</p>;
  if (!data) return <p className="text-sm opacity-70">{t("loading")}</p>;

  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h1 className="text-lg font-semibold">{t("usageTitle")}</h1>
        <p className="text-xs opacity-70">
          {t("usageWindow", {
            from: stamp(data.window_start),
            to: stamp(data.window_end),
          })}
          {data.enforced ? "" : ` · ${t("usageNotEnforced")}`}
        </p>
      </header>

      {/* A ceiling nobody is told about is an outage with no explanation. */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">{t("usageCeilings")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {data.ceilings.map((c) => (
            <div key={c.name} className="rounded-md border border-border p-3">
              <p className="text-xs uppercase opacity-60">{t(ceilingKey(c.name))}</p>
              <p className="text-base font-semibold">
                {usd(c.spent_uusd)}
                {c.ceiling_uusd === null ? (
                  <span className="ml-2 text-xs font-normal opacity-60">{t("usageCeilingOff")}</span>
                ) : (
                  <span className="ml-2 text-xs font-normal opacity-60">
                    / {usd(c.ceiling_uusd)} · {pct(c.share)}
                  </span>
                )}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* The question this page exists to answer. */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">{t("usageTopSubjects")}</h2>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="py-2 font-medium">{t("usageColWho")}</th>
                <th className="py-2 font-medium">{t("usageColTier")}</th>
                <th className="py-2 text-right font-medium">{t("usageColCredits")}</th>
                <th className="py-2 text-right font-medium">{t("usageColCost")}</th>
                <th className="py-2 text-right font-medium">{t("usageColTurns")}</th>
                <th className="py-2 text-right font-medium">{t("usageColRefusals")}</th>
              </tr>
            </thead>
            <tbody>
              {data.top_subjects.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-2 text-muted-foreground">{t("usageNobody")}</td>
                </tr>
              ) : (
                data.top_subjects.map((row) => (
                  <tr key={`${row.subject_kind}:${row.subject_id}`}>
                    <td className="py-2">
                      {row.subject_kind === "user" ? (
                        <Link href={`/admin/users/${row.subject_id}`} className="underline">
                          {row.email ?? row.subject_id}
                        </Link>
                      ) : (
                        <span className="opacity-70">
                          {t(kindKey(row.subject_kind))} {row.subject_id.slice(0, 8)}
                        </span>
                      )}
                    </td>
                    <td className="py-2">{t(tierKey(row.tier))}</td>
                    <td className="py-2 text-right">
                      {row.credits_used}
                      {row.limit_credits !== null ? (
                        <span className="opacity-50"> / {row.limit_credits}</span>
                      ) : null}
                      {row.over_limit ? <span className="ml-1 text-destructive">●</span> : null}
                    </td>
                    <td className="py-2 text-right tabular-nums">{usd(row.uusd_used)}</td>
                    <td className="py-2 text-right">
                      {row.turns_used}
                      {row.turn_limit !== null ? (
                        <span className="opacity-50"> / {row.turn_limit}</span>
                      ) : null}
                    </td>
                    <td className="py-2 text-right">{row.refusals || ""}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="text-xs opacity-60">{t("usageTopNote")}</p>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">{t("usageByTier")}</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 font-medium">{t("usageColTier")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColSubjects")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColCost")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColTurns")}</th>
                </tr>
              </thead>
              <tbody>
                {data.by_tier.map((row) => (
                  <tr key={row.tier}>
                    <td className="py-2">{t(tierKey(row.tier))}</td>
                    <td className="py-2 text-right">{row.subjects}</td>
                    <td className="py-2 text-right tabular-nums">{usd(row.uusd_used)}</td>
                    <td className="py-2 text-right">{row.turns_used}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-semibold">{t("usageByPurpose")}</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <tbody>
                {data.by_purpose.length === 0 ? (
                  <tr><td className="opacity-60">{t("usageNobody")}</td></tr>
                ) : (
                  data.by_purpose.map(([purpose, cost]) => (
                    <tr key={purpose}>
                      <td className="py-2">{purpose}</td>
                      <td className="py-2 text-right tabular-nums">{usd(cost)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* The number the tiers should be tuned from. */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">{t("usageRefusalsTitle")}</h2>
        {data.refusals.length === 0 ? (
          <p className="text-xs opacity-60">{t("usageNoRefusals")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 font-medium">{t("usageColTier")}</th>
                  <th className="py-2 font-medium">{t("usageColLimit")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColCount")}</th>
                </tr>
              </thead>
              <tbody>
                {data.refusals.map((row) => (
                  <tr key={`${row.tier}:${row.limit_kind}`}>
                    <td className="py-2">{t(tierKey(row.tier))}</td>
                    <td className="py-2"><code>{row.limit_kind}</code></td>
                    <td className="py-2 text-right">{row.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs opacity-60">{t("usageRefusalsNote")}</p>
      </section>

      {/* Either telemetry was lost, or something billed without metering. The
          second is the failure the context-manager seam cannot rule out. */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">{t("usageGapsTitle")}</h2>
        {data.gaps.length === 0 ? (
          <p className="text-xs opacity-60">{t("usageNoGaps")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 font-medium">{t("usageColWho")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColCounter")}</th>
                  <th className="py-2 text-right font-medium">{t("usageColTelemetry")}</th>
                </tr>
              </thead>
              <tbody>
                {data.gaps.map((row) => (
                  <tr key={`${row.subject_kind}:${row.subject_id}`}>
                    <td className="py-2">{row.subject_kind} {row.subject_id.slice(0, 8)}</td>
                    <td className="py-2 text-right tabular-nums">{usd(row.counter_uusd)}</td>
                    <td className="py-2 text-right tabular-nums">{usd(row.llm_call_uusd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs opacity-60">{t("usageGapsNote")}</p>
      </section>
    </div>
  );
}
