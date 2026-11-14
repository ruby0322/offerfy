"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import {
  confirmApplied,
  markApplied,
  patchApplication,
  type Application,
  type ApplicationStatus,
} from "@/lib/applications";
import {
  byUrgency,
  nextAction,
  quietDays,
  QUIET_DAYS,
  readyAgeDays,
  responseChoices,
} from "./applications";

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

const ACTION = "inline-block border border-current px-3 py-1.5 text-sm disabled:opacity-50";

const STAGE_KEY: Record<string, string> = {
  interviewing: "groupInterviewing",
  applied: "groupApplied",
  tailored: "groupTailored",
  saved: "groupSaved",
  closed: "groupClosed",
};

/** Chip colour by stage: forward is green, in-flight clay, finished muted. */
const STAGE_CLASS: Record<string, string> = {
  interviewing: "border-success text-success",
  applied: "border-primary text-primary",
  tailored: "border-foreground text-foreground",
  saved: "border-rule text-muted-foreground",
  closed: "border-rule text-muted-foreground",
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
  const tJobs = useTranslations("jobs");
  const [rows, setRows] = useState<Application[]>(initial);
  const [filter, setFilter] = useState<Filter>("needsYou");
  const [choosingId, setChoosingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
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

  async function run(app: Application, op: () => Promise<Application>) {
    setBusyId(app.job_id);
    setFailedId(null);
    try {
      replace(await op());
      setChoosingId(null);
    } catch {
      setFailedId(app.job_id);
    } finally {
      setBusyId(null);
    }
  }

  function action(app: Application) {
    const busy = busyId === app.job_id;
    switch (nextAction(app)) {
      case "tailor":
        return (
          <Link className={ACTION} href={`/jobs/${encodeURIComponent(app.job_id)}`}>
            {t("actionTailor")}
          </Link>
        );
      case "apply":
        return (
          <a
            className={ACTION}
            href={app.apply_url}
            rel="noopener noreferrer"
            target="_blank"
            // Fire and forget: the link must open whether or not this lands.
            onClick={() => {
              markApplied(app.job_id).then(replace).catch(() => {});
            }}
          >
            {t("actionApply")}
          </a>
        );
      case "confirm":
        return (
          <span className="inline-flex flex-wrap items-center justify-end gap-2 text-sm">
            <span className="text-muted-foreground">{t("didYouApply")}</span>
            <button
              type="button"
              className={ACTION}
              disabled={busy}
              onClick={() => run(app, () => confirmApplied(app.job_id, true))}
            >
              {t("yes")}
            </button>
            <button
              type="button"
              className={ACTION}
              disabled={busy}
              onClick={() => run(app, () => confirmApplied(app.job_id, false))}
            >
              {t("no")}
            </button>
          </span>
        );
      case "logResponse":
        // Closing now has two buttons, which differ only in whether anyone
        // replied -- the one fact the status cannot carry afterwards.
        return choosingId === app.job_id ? (
          <span className="inline-flex flex-wrap items-center justify-end gap-2">
            {responseChoices(app).map((c) => (
              <button
                key={c.key}
                type="button"
                className={ACTION}
                disabled={busy}
                onClick={() =>
                  run(app, () =>
                    patchApplication(app.job_id, {
                      status: c.status,
                      ...(c.replied === undefined ? {} : { replied: c.replied }),
                    }),
                  )
                }
              >
                {t(c.key)}
              </button>
            ))}
          </span>
        ) : (
          <button
            type="button"
            className={ACTION}
            onClick={() => setChoosingId(app.job_id)}
          >
            {t("actionLogResponse")}
          </button>
        );
      default:
        return <span className="text-muted-foreground">{t("metricNone")}</span>;
    }
  }

  /** The one time figure that matters for this row's stage. */
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
                  ? "rounded-full border border-foreground bg-foreground px-3 py-1 text-xs font-semibold text-background"
                  : "rounded-full border border-rule px-3 py-1 text-xs font-semibold text-muted-foreground"
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
                    {waiting(app)}
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
                    {action(app)}
                    {failedId === app.job_id ? (
                      <p className="mt-1 text-xs text-destructive" role="alert">
                        {tJobs("actionFailed")}
                      </p>
                    ) : null}
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
