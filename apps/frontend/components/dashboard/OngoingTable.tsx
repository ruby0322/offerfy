"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";
import type { Application } from "@/lib/applications";
import ApplicationAction from "./ApplicationAction";
import ApplicationCard, { StageChip, WaitingLabel } from "./ApplicationCard";
import { byUrgency } from "./applications";

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

type Props = {
  rows: Application[];
  /** Hands the updated row back so the overview can replace it in place. */
  onChanged: (next: Application) => void;
};

export default function OngoingTable({ rows, onChanged }: Props) {
  const t = useTranslations("dashboard");
  const narrow = useMediaQuery(NARROW_SCREEN);
  const now = useMemo(() => new Date(), []);
  const ongoing = useMemo(
    () => byUrgency(rows.filter((r) => r.status !== "closed"), now),
    [rows, now],
  );
  const shown = ongoing.slice(0, SHOWN);

  if (ongoing.length === 0) return null;

  return (
    <div className="mt-4 border-t border-rule pt-3" data-testid="dashboard-ongoing">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
        {t("ongoingHeading")} · {ongoing.length}
      </p>
      {narrow ? (
        <ul className="mt-1 min-w-0" data-testid="ongoing-cards">
          {shown.map((app) => (
            <ApplicationCard key={app.job_id} app={app} now={now} onChanged={onChanged} />
          ))}
        </ul>
      ) : (
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
                  <StageChip status={app.status} />
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
                  <WaitingLabel app={app} now={now} />
                </td>
                <td className="whitespace-nowrap py-1.5 text-right align-middle">
                  <ApplicationAction
                    app={app}
                    onChanged={onChanged}
                    emptyLabel={t("metricNone")}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem] text-muted-foreground">
        <span>{t("ongoingShowing", { shown: shown.length, total: ongoing.length })}</span>
        <Link href="/dashboard/applications" className="underline text-foreground">
          {t("openStatusTable")}
        </Link>
      </div>
    </div>
  );
}
