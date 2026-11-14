"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { applicationMetrics, QUIET_DAYS } from "./applicationMetrics";
import type { Application } from "@/lib/applications";

/**
 * The five numbers the applications page opens with.
 *
 * They live here rather than on the dashboard overview on purpose: these are
 * figures you study, and the overview is a place you glance at. Each is
 * chosen on one test -- does the number change what you do? Reply rate judges
 * the resume rather than the effort; waiting and never-sent are the two
 * things only the person can clear; interviews are the outcome; the median
 * says whether eleven days of silence means anything yet.
 *
 * A tile's second line is where its warning lives, so trouble is visible
 * without reading a single row below.
 */
export default function ApplicationMetrics({ rows }: { rows: Application[] }) {
  const t = useTranslations("dashboard");
  const m = useMemo(() => applicationMetrics(rows), [rows]);

  const tiles: {
    key: string;
    label: string;
    value: string;
    unit?: string;
    hint: string;
    tone: "plain" | "warn" | "good";
  }[] = [
    {
      key: "replyRate",
      label: t("metricReplyRate"),
      // Null, not 0: "0%" from nothing sent reads as a verdict on the resume.
      value: m.replyRate === null ? t("metricNone") : String(m.replyRate),
      unit: m.replyRate === null ? undefined : "%",
      hint:
        m.sent === 0
          ? t("metricReplyRateNone")
          : t("metricReplyRateOf", { replied: m.replied, sent: m.sent }),
      tone: "plain",
    },
    {
      key: "waiting",
      label: t("metricWaiting"),
      value: String(m.waiting),
      hint:
        m.waitingTooLong > 0
          ? t("metricWaitingQuiet", { count: m.waitingTooLong, days: QUIET_DAYS })
          : t("metricWaitingCalm"),
      tone: m.waitingTooLong > 0 ? "warn" : "plain",
    },
    {
      key: "neverSent",
      label: t("metricNeverSent"),
      value: String(m.neverSent),
      hint:
        m.neverSentOldestDays === null
          ? t("metricNone")
          : t("metricNeverSentOldest", { days: m.neverSentOldestDays }),
      tone: m.neverSent > 0 ? "warn" : "plain",
    },
    {
      key: "interviewing",
      label: t("metricInterviewing"),
      value: String(m.interviewing),
      hint: t("metricNone"),
      tone: m.interviewing > 0 ? "good" : "plain",
    },
    {
      key: "median",
      label: t("metricMedianReply"),
      value:
        m.medianDaysToReply === null ? t("metricNone") : String(m.medianDaysToReply),
      hint:
        m.replied === 0 ? t("metricNone") : t("metricMedianReplyOf", { count: m.replied }),
      tone: "plain",
    },
  ];

  return (
    <section
      aria-labelledby="applications-metrics"
      data-testid="application-metrics"
      className="overflow-hidden rounded-lg border border-rule bg-rule"
    >
      <h2 id="applications-metrics" className="sr-only">
        {t("tableHeading")}
      </h2>
      {/* One-pixel gaps come from the container's background showing through,
          so the tiles read as one object rather than five cards. */}
      <div className="grid grid-cols-2 gap-px sm:grid-cols-3 lg:grid-cols-5">
        {tiles.map((tile) => (
          <div key={tile.key} className="min-w-0 bg-sheet px-4 py-3">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
              {tile.label}
            </p>
            <p className="mt-1 font-display text-[1.8rem] font-medium leading-none tracking-[-0.03em] [font-variant-numeric:tabular-nums]">
              {tile.value}
              {tile.unit ? (
                <span className="ml-0.5 font-sans text-sm font-semibold text-muted-foreground">
                  {tile.unit}
                </span>
              ) : null}
            </p>
            <p
              className={
                tile.tone === "warn"
                  ? "mt-1.5 flex items-center gap-1.5 text-xs font-semibold text-warn"
                  : tile.tone === "good"
                    ? "mt-1.5 text-xs font-semibold text-success"
                    : "mt-1.5 text-xs text-muted-foreground"
              }
            >
              {tile.tone === "warn" ? (
                <span
                  aria-hidden="true"
                  className="size-1.5 shrink-0 rounded-full bg-current"
                />
              ) : null}
              {tile.hint}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
