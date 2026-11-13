"use client";

import IngestFlow from "@/components/admin/IngestFlow";
import {
  formatAgeHours,
  formatDuration,
  ingestVerdict,
  isBadVerdict,
  latestFailingBoards,
} from "@/lib/admin-ingest";
import {
  adminJobsIngest,
  adminJobsIngestPass,
  type AdminIngestOverview,
  type AdminIngestRun,
} from "@/lib/api";
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
  // The flow diagram is per pass, so one has to be chosen. The newest is the
  // question anyone opening this page is actually asking.
  const [runId, setRunId] = useState<string | null>(null);
  // Keyed by the pass it belongs to rather than cleared on every change: a
  // synchronous setState inside the effect below is a cascading render, and
  // comparing run ids gives the same "loading" state for free. `items: null`
  // is the request having failed for that pass.
  const [loaded, setLoaded] = useState<{
    runId: string;
    items: AdminIngestRun[] | null;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminJobsIngest()
      .then((next) => {
        if (cancelled) return;
        setData(next);
        setRunId(next.passes[0]?.run_id ?? null);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (runId === null) return;
    let cancelled = false;
    adminJobsIngestPass(runId)
      .then((next) => {
        if (!cancelled) setLoaded({ runId, items: next.items });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ runId, items: null });
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  const { health, passes } = data;
  const forThisPass = loaded?.runId === runId ? loaded : null;
  const rows = forThisPass?.items ?? null;
  const rowsError = forThisPass !== null && forThisPass.items === null;
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

      {passes.length > 0 ? (
        <section className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">{t("flowTitle")}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t("flowHint")}</p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">{t("flowPass")}</span>
              <select
                className="rounded-md border border-border bg-sheet px-2 py-1 text-sm"
                value={runId ?? ""}
                onChange={(event) => setRunId(event.target.value)}
              >
                {passes.map((row) => (
                  <option key={row.run_id} value={row.run_id}>
                    {`${stamp(row.started_at)} · ${t(`ingestStatus_${row.status}`)}`}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {rowsError ? (
            <p className="text-sm text-destructive">{t("error")}</p>
          ) : rows === null ? (
            <p className="text-sm text-muted-foreground">{t("loading")}</p>
          ) : (
            <IngestFlow rows={rows} />
          )}
        </section>
      ) : null}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">{t("ingestPasses")}</h2>
        <p className="text-sm text-muted-foreground">{t("ingestPassesHint")}</p>
        <p className="text-sm text-muted-foreground">{t("ingestScopeHint")}</p>
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
                  <th className="py-2 font-medium">{t("ingestFetched")}</th>
                  <th className="py-2 font-medium">{t("ingestUpserted")}</th>
                  <th className="py-2 font-medium">{t("ingestOutOfScope")}</th>
                  <th className="py-2 font-medium">{t("ingestStatus")}</th>
                </tr>
              </thead>
              <tbody>
                {passes.map((row) => (
                  <tr
                    key={row.run_id}
                    className={cn(
                      "border-b border-border/70",
                      row.run_id === runId && "bg-hover",
                    )}
                  >
                    <td className="py-2">
                      <button
                        type="button"
                        onClick={() => setRunId(row.run_id)}
                        aria-pressed={row.run_id === runId}
                        className="underline-offset-4 hover:underline"
                        title={t("flowSelectPass")}
                      >
                        {stamp(row.started_at)}
                      </button>
                    </td>
                    <td className="py-2">{formatDuration(row.duration_seconds)}</td>
                    <td className="py-2">{row.sources_ok}</td>
                    <td className={cn("py-2", row.sources_failed > 0 && "text-destructive")}>
                      {row.sources_failed}
                    </td>
                    <td className="py-2">{row.fetched}</td>
                    <td className="py-2">{row.upserted}</td>
                    <td className="py-2 text-muted-foreground">
                      {Math.max(row.fetched - row.upserted, 0)}
                    </td>
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
