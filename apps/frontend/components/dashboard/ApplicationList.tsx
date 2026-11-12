"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import {
  confirmApplied,
  groupApplications,
  markApplied,
  patchApplication,
  type Application,
} from "@/lib/applications";
import { nextAction, quietDays, readyAgeDays, responseChoices } from "./applications";

const ACTION = "inline-block border border-current px-3 py-1.5 text-sm disabled:opacity-50";

const GROUP_KEYS = {
  interviewing: "groupInterviewing",
  applied: "groupApplied",
  tailored: "groupTailored",
  saved: "groupSaved",
  closed: "groupClosed",
} as const;

type Props = { initial: Application[] };

export default function ApplicationList({ initial }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const [rows, setRows] = useState<Application[]>(initial);
  // A refetched dashboard hands down new rows; useState alone keeps the first.
  useEffect(() => setRows(initial), [initial]);
  const [choosingId, setChoosingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const now = useMemo(() => new Date(), []);
  // Includes the trailing group: a status GROUP_ORDER lacks (closed) still shows.
  const groups = useMemo(() => groupApplications(rows), [rows]);

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
          <span className="flex items-center gap-2 text-sm">
            <span>{t("didYouApply")}</span>
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
        // One primary action; it opens a choice so the person says what the
        // response was. This is the only place interviewing and closed are written.
        return choosingId === app.job_id ? (
          <span className="flex items-center gap-2 text-sm">
            {responseChoices(app).map((c) => (
              <button
                key={c.status}
                type="button"
                className={ACTION}
                disabled={busy}
                onClick={() => run(app, () => patchApplication(app.job_id, { status: c.status }))}
              >
                {t(c.key)}
              </button>
            ))}
          </span>
        ) : (
          <button type="button" className={ACTION} onClick={() => setChoosingId(app.job_id)}>
            {t("actionLogResponse")}
          </button>
        );
      default:
        return null;
    }
  }

  return (
    <section data-testid="dashboard-applications" aria-labelledby="dash-applications">
      <h2 id="dash-applications" className="mb-3 text-lg font-semibold tracking-tight">
        {t("headingApplications")}
      </h2>
      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="dashboard-applications-empty">
          {t("applicationsEmpty")}
        </p>
      ) : (
        <div className="grid gap-5">
          {groups.map((group) => {
            const key = GROUP_KEYS[group.status as keyof typeof GROUP_KEYS];
            return (
              <div key={group.status} data-testid={`dashboard-group-${group.status}`}>
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {/* A status this build does not know keeps its raw name. */}
                  {key ? t(key) : group.status} {"·"} {group.items.length}
                </h3>
                <ul className="grid gap-2">
                  {group.items.map((app) => {
                    const ready = readyAgeDays(app, now);
                    return (
                      <li
                        key={app.job_id}
                        className="rr-card rr-row-card flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-3"
                        data-testid="dashboard-application"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-medium tracking-tight">
                            <Link href={`/jobs/${encodeURIComponent(app.job_id)}`}>
                              {app.job_title}
                            </Link>
                            <span className="font-normal text-muted-foreground"> {"·"} {app.company}</span>
                          </p>
                          <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                            {ready !== null ? (
                              <span className="font-semibold text-foreground" data-testid="dashboard-ready-age">
                                {t("readyAgo", { count: ready })}
                              </span>
                            ) : null}
                            {app.status === "applied" ? (
                              <span>{t("quietDays", { count: quietDays(app, now) })}</span>
                            ) : null}
                            {!app.job_is_active ? <span>{t("jobInactive")}</span> : null}
                            {app.tailor_resume_id ? (
                              <Link className="underline" href={`/editor/${app.tailor_resume_id}`}>
                                {tJobs("openTailor")}
                              </Link>
                            ) : null}
                          </p>
                          {failedId === app.job_id ? (
                            <p className="mt-1 text-xs text-destructive" role="alert">
                              {tJobs("actionFailed")}
                            </p>
                          ) : null}
                        </div>
                        <div className="shrink-0">{action(app)}</div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
