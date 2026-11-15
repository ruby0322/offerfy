"use client";

import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";
import type { Application } from "@/lib/applications";
import ApplicationAction from "./ApplicationAction";
import { quietDays, QUIET_DAYS, readyAgeDays } from "./applications";

/**
 * One application as a card, for the width where six columns do not fit.
 *
 * The status table is stage, role, waiting, source, resume, action. At 390px
 * that is about 65px a column, so below NARROW_SCREEN the rows become two
 * lines: the mark and the role, then the stage, how long it has been waiting
 * and the one action. `source` and `resume` are the two you read *down* a
 * column rather than across a row -- which channel gets replies, which rows
 * have a tailored resume -- so they lose least by staying on the wide layout.
 *
 * The chip and the waiting label are exported because the wide tables draw
 * the same two things. They were written twice, once per table, and had
 * already drifted: only one of them knew about `closed`.
 */

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

export function StageChip({ status }: { status: string }) {
  const t = useTranslations("dashboard");
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${
        STAGE_CLASS[status] ?? "border-rule text-muted-foreground"
      }`}
    >
      {STAGE_KEY[status] ? t(STAGE_KEY[status] as "groupApplied") : status}
    </span>
  );
}

/**
 * How long this row has been waiting on someone, and whether that is too
 * long. Amber is reserved for the two waits the person can actually clear.
 */
export function WaitingLabel({ app, now }: { app: Application; now: Date }) {
  const t = useTranslations("dashboard");
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
      <span className={quiet > QUIET_DAYS ? "font-semibold text-warn" : "text-muted-foreground"}>
        {t("quietDays", { count: quiet })}
      </span>
    );
  }
  return <span className="text-muted-foreground">{t("metricNone")}</span>;
}

export default function ApplicationCard({
  app,
  now,
  onChanged,
}: {
  app: Application;
  now: Date;
  onChanged: (next: Application) => void;
}) {
  const t = useTranslations("dashboard");
  return (
    <li
      className="flex min-w-0 flex-col gap-2 border-b border-rule py-3 last:border-b-0"
      data-testid="application-row"
    >
      <span className="flex min-w-0 items-center gap-2">
        <EmployerLogo company={app.company} size="sm" />
        <span className="min-w-0 flex-1">
          <Link
            href={`/jobs/${encodeURIComponent(app.job_id)}`}
            className="block truncate text-sm font-medium"
          >
            {app.job_title}
          </Link>
          <span className="block truncate text-xs text-muted-foreground">
            {app.company}
            {app.job_is_active ? "" : ` · ${t("jobInactive")}`}
          </span>
        </span>
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <StageChip status={app.status} />
        <span className="text-xs [font-variant-numeric:tabular-nums]">
          <WaitingLabel app={app} now={now} />
        </span>
        {/* Pushed to the row's end so every card's action lands in the same
            place down the list, whatever the stage label's width. */}
        <span className="ml-auto">
          <ApplicationAction app={app} onChanged={onChanged} emptyLabel={t("metricNone")} />
        </span>
      </span>
    </li>
  );
}
