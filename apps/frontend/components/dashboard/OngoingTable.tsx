"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import type { Application } from "@/lib/applications";
import { byUrgency, nextAction, quietDays, QUIET_DAYS, readyAgeDays } from "./applications";

/**
 * The few applications that need you, under the diagram on the overview.
 *
 * Four columns and five rows: it answers the question the 分流圖 raises
 * ("which ones?") without leaving the overview. Closed rows are deliberately
 * absent -- they are history, and history belongs on the tab with room for
 * it, which is also where the filters, the source column and the per-row
 * actions live. This is a read, not a control surface; the one link goes to
 * the posting.
 */

const SHOWN = 5;

export default function OngoingTable({ rows }: { rows: Application[] }) {
  const t = useTranslations("dashboard");
  const now = useMemo(() => new Date(), []);
  const ongoing = useMemo(
    () => byUrgency(rows.filter((r) => r.status !== "closed"), now),
    [rows, now],
  );
  const shown = ongoing.slice(0, SHOWN);

  if (ongoing.length === 0) return null;

  function waiting(app: Application) {
    const ready = readyAgeDays(app, now);
    if (ready !== null) {
      return (
        <span className={ready > 2 ? "font-semibold text-warn" : "text-muted-foreground"}>
          {t("readyAgo", { count: ready })}
        </span>
      );
    }
    if (app.status === "applied") {
      const quiet = quietDays(app, now);
      return (
        <span
          className={quiet > QUIET_DAYS ? "font-semibold text-warn" : "text-muted-foreground"}
        >
          {t("quietDays", { count: quiet })}
        </span>
      );
    }
    return <span className="text-muted-foreground">{t("metricNone")}</span>;
  }

  const STAGE_KEY: Record<string, string> = {
    interviewing: "groupInterviewing",
    applied: "groupApplied",
    tailored: "groupTailored",
    saved: "groupSaved",
  };
  const STAGE_CLASS: Record<string, string> = {
    interviewing: "border-success text-success",
    applied: "border-primary text-primary",
    tailored: "border-foreground text-foreground",
    saved: "border-rule text-muted-foreground",
  };

  return (
    <div className="mt-4 border-t border-rule pt-3" data-testid="dashboard-ongoing">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
        {t("ongoingHeading")} · {ongoing.length}
      </p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule text-left">
              {(["colStage", "colRole", "colWaiting"] as const).map((key) => (
                <th
                  key={key}
                  className="whitespace-nowrap pb-1.5 pr-3 text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground"
                >
                  {t(key)}
                </th>
              ))}
              <th className="whitespace-nowrap pb-1.5 text-right text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
                {t("colNext")}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((app) => (
              <tr key={app.job_id} className="border-b border-rule last:border-b-0">
                <td className="py-1.5 pr-3 align-middle">
                  <span
                    className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${
                      STAGE_CLASS[app.status] ?? "border-rule text-muted-foreground"
                    }`}
                  >
                    {STAGE_KEY[app.status]
                      ? t(STAGE_KEY[app.status] as "groupApplied")
                      : app.status}
                  </span>
                </td>
                <td className="min-w-[12rem] py-1.5 pr-3 align-middle">
                  <span className="flex items-center gap-2">
                    {/* The same recognition cue the match rows use, so both
                        lists in this card scan the same way. */}
                    <EmployerLogo company={app.company} size="sm" />
                    <span className="min-w-0">
                      <Link
                        href={`/jobs/${encodeURIComponent(app.job_id)}`}
                        className="block truncate font-medium"
                      >
                        {app.job_title}
                      </Link>
                      <span className="block truncate text-xs text-muted-foreground">
                        {app.company}
                      </span>
                    </span>
                  </span>
                </td>
                <td className="whitespace-nowrap py-1.5 pr-3 align-middle [font-variant-numeric:tabular-nums]">
                  {waiting(app)}
                </td>
                <td className="whitespace-nowrap py-1.5 text-right align-middle text-xs text-muted-foreground">
                  {nextAction(app) === "tailor"
                    ? t("actionTailor")
                    : nextAction(app) === "apply"
                      ? t("actionApply")
                      : nextAction(app) === "confirm"
                        ? t("didYouApply")
                        : nextAction(app) === "logResponse"
                          ? t("actionLogResponse")
                          : t("metricNone")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem] text-muted-foreground">
        <span>{t("ongoingShowing", { shown: shown.length, total: ongoing.length })}</span>
        <Link href="/dashboard/applications" className="underline text-foreground">
          {t("openStatusTable")}
        </Link>
      </div>
    </div>
  );
}
