"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";
import type { Application, ApplicationStatus } from "@/lib/applications";
import ApplicationAction from "./ApplicationAction";
import ApplicationCard, { StageChip, WaitingLabel } from "./ApplicationCard";
import { byUrgency, nextAction } from "./applications";

/**
 * Every application as one table, sorted by what needs the person.
 *
 * This replaces five stacked lists of cards: twelve applications fit in a
 * screen instead of three, the waiting time gets a column you can read down,
 * and `source` is here because read as a column it answers a question no
 * single row can -- which channel actually gets replies.
 *
 * Each row keeps exactly the one action it had before; the vocabulary of
 * transitions still lives in ./applications and the service behind it.
 */

/** The filter chips name stages, so they share the table's own vocabulary. */
const STAGE_KEY: Record<string, string> = {
  interviewing: "groupInterviewing",
  applied: "groupApplied",
  tailored: "groupTailored",
  saved: "groupSaved",
  closed: "groupClosed",
};

type Filter = "needsYou" | "all" | ApplicationStatus;

const FILTERS: Filter[] = [
  "needsYou",
  "interviewing",
  "applied",
  "tailored",
  "saved",
  "closed",
  "all",
];

function matches(app: Application, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "needsYou") return nextAction(app) !== null;
  return app.status === filter;
}

export default function ApplicationTable({ rows: initial }: { rows: Application[] }) {
  const t = useTranslations("dashboard");
  const narrow = useMediaQuery(NARROW_SCREEN);
  const [rows, setRows] = useState<Application[]>(initial);
  const [filter, setFilter] = useState<Filter>("needsYou");
  // One clock for the whole render, so two rows one second apart cannot
  // disagree about whether they are past the quiet line.
  const now = useMemo(() => new Date(), []);
  const ordered = useMemo(() => byUrgency(rows, now), [rows, now]);
  const shown = useMemo(
    () => ordered.filter((app) => matches(app, filter)),
    [ordered, filter],
  );
  const counts = useMemo(() => {
    const out = new Map<Filter, number>();
    for (const f of FILTERS) out.set(f, rows.filter((app) => matches(app, f)).length);
    return out;
  }, [rows]);

  function replace(next: Application) {
    setRows((prev) => prev.map((r) => (r.job_id === next.job_id ? next : r)));
  }

  return (
    <section
      aria-labelledby="applications-table"
      data-testid="application-table"
      className="rr-card px-5 py-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h2 id="applications-table" className="text-sm font-semibold tracking-tight">
          {t("tableHeading")}
        </h2>
        <p className="text-xs text-muted-foreground [font-variant-numeric:tabular-nums]">
          {t("showingCount", { shown: shown.length, total: rows.length })}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {FILTERS.filter((f) => f === "needsYou" || f === "all" || (counts.get(f) ?? 0) > 0).map(
          (f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={
                filter === f
                  ? "whitespace-nowrap rounded-full border border-foreground bg-foreground px-3 py-1 text-xs font-semibold coarse:px-3.5 coarse:py-2 text-background"
                  : "whitespace-nowrap rounded-full border border-rule px-3 py-1 text-xs font-semibold coarse:px-3.5 coarse:py-2 text-muted-foreground"
              }
            >
              {f === "needsYou"
                ? t("filterNeedsYou")
                : f === "all"
                  ? t("filterAll")
                  : t(STAGE_KEY[f] as "groupApplied")}{" "}
              <span className="font-normal opacity-70">{counts.get(f) ?? 0}</span>
            </button>
          ),
        )}
      </div>

      {shown.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground" data-testid="application-table-empty">
          {rows.length === 0 ? t("applicationsEmpty") : t("filterNeedsYou")}
        </p>
      ) : narrow ? (
        <ul className="mt-2 min-w-0" data-testid="application-cards">
          {shown.map((app) => (
            <ApplicationCard key={app.job_id} app={app} now={now} onChanged={replace} />
          ))}
        </ul>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-rule text-left">
                {(["colStage", "colRole", "colWaiting", "colSource", "colResume"] as const).map(
                  (key) => (
                    <th
                      key={key}
                      className="whitespace-nowrap pb-2 pr-3 text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground"
                    >
                      {t(key)}
                    </th>
                  ),
                )}
                <th className="whitespace-nowrap pb-2 text-right text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
                  {t("colNext")}
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((app) => (
                <tr
                  key={app.job_id}
                  className="border-b border-rule last:border-b-0"
                  data-testid="application-row"
                >
                  <td className="py-2 pr-3 align-middle">
                    <StageChip status={app.status} />
                  </td>
                  <td className="min-w-[13rem] py-2 pr-3 align-middle">
                    <span className="flex items-center gap-2">
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
                          {app.job_is_active ? "" : ` · ${t("jobInactive")}`}
                        </span>
                      </span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap py-2 pr-3 align-middle [font-variant-numeric:tabular-nums]">
                    <WaitingLabel app={app} now={now} />
                  </td>
                  <td className="whitespace-nowrap py-2 pr-3 align-middle text-xs text-muted-foreground">
                    {app.source === "digest" ? t("sourceDigest") : t("sourceSearch")}
                  </td>
                  <td className="whitespace-nowrap py-2 pr-3 align-middle text-xs">
                    {app.tailor_resume_id ? (
                      <Link
                        href={`/editor/${app.tailor_resume_id}`}
                        className="underline underline-offset-2"
                      >
                        {t("tailorBadge")}
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">{t("metricNone")}</span>
                    )}
                  </td>
                  <td className="py-2 text-right align-middle">
                    <ApplicationAction
                      app={app}
                      onChanged={replace}
                      emptyLabel={t("metricNone")}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
