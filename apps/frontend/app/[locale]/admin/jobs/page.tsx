"use client";

import {
  formatAgeHours,
  formatDuration,
  ingestVerdict,
  isBadVerdict,
  latestFailingBoards,
} from "@/lib/admin-ingest";
import { adminJobsIngest, type AdminIngestOverview } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

function stamp(value: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminJobsIngestPage() {
  const t = useTranslations("admin");
  const [data, setData] = useState<AdminIngestOverview | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminJobsIngest()
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  const { health, passes } = data;
  const verdict = ingestVerdict(health);
  const bad = isBadVerdict(verdict);
  const failing = latestFailingBoards(passes);

  const cards = [
    [t("ingestAge"), formatAgeHours(health.catalog_age_hours)],
    [t("ingestFresh"), health.fresh_job_count],
    [t("ingestStale"), health.stale_job_count],
    [t("ingestCadence"), `${health.expected_interval_hours}h`],
  ] as const;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">{t("jobs")}</h1>

      <section className="space-y-3">
        <div
          className={cn(
            "rounded-lg border p-4",
            bad ? "border-destructive/60 bg-destructive/5" : "border-border bg-sheet",
          )}
        >
          <p className={cn("text-lg font-semibold", bad && "text-destructive")}>
            {t(`ingestVerdict_${verdict}`)}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("ingestVerdictHint", { days: health.freshness_days })}
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {cards.map(([label, value]) => (
            <div key={label} className="rounded-lg border border-border bg-sheet p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-semibold">{value}</p>
            </div>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">
          {t("ingestFreshest")} {stamp(health.freshest_last_seen_at)}
          {health.last_failure_at ? (
            <>
              {" · "}
              <span className="text-destructive">
                {t("ingestLastFailure")} {stamp(health.last_failure_at)}
              </span>
            </>
          ) : null}
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">{t("ingestPasses")}</h2>
        <p className="text-sm text-muted-foreground">{t("ingestPassesHint")}</p>
        {passes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("ingestNoPasses")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 font-medium">{t("ingestStarted")}</th>
                  <th className="py-2 font-medium">{t("ingestDuration")}</th>
                  <th className="py-2 font-medium">{t("ingestSourcesOk")}</th>
                  <th className="py-2 font-medium">{t("ingestSourcesFailed")}</th>
                  <th className="py-2 font-medium">{t("ingestUpserted")}</th>
                  <th className="py-2 font-medium">{t("ingestStatus")}</th>
                </tr>
              </thead>
              <tbody>
                {passes.map((row) => (
                  <tr key={row.run_id} className="border-b border-border/70">
                    <td className="py-2">{stamp(row.started_at)}</td>
                    <td className="py-2">{formatDuration(row.duration_seconds)}</td>
                    <td className="py-2">{row.sources_ok}</td>
                    <td className={cn("py-2", row.sources_failed > 0 && "text-destructive")}>
                      {row.sources_failed}
                    </td>
                    <td className="py-2">{row.upserted}</td>
                    <td
                      className={cn("py-2", row.status !== "ok" && "text-destructive")}
                    >
                      {t(`ingestStatus_${row.status}`)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.legacy_row_count > 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("ingestLegacyRows", { count: data.legacy_row_count })}
          </p>
        ) : null}
      </section>

      {failing ? (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">{t("ingestFailingBoards")}</h2>
          <p className="text-sm text-muted-foreground">{t("ingestFailingBoardsHint")}</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 font-medium">{t("source")}</th>
                  <th className="py-2 font-medium">{t("ingestBoard")}</th>
                  <th className="py-2 font-medium">{t("ingestBoardError")}</th>
                </tr>
              </thead>
              <tbody>
                {failing.failing_boards.map((board) => (
                  <tr
                    key={`${board.source}:${board.board_slug ?? ""}`}
                    className="border-b border-border/70"
                  >
                    <td className="py-2">{board.source}</td>
                    <td className="py-2">{board.board_slug ?? "—"}</td>
                    <td className="py-2 text-muted-foreground">{board.error_snippet ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
