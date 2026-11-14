"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { confirmApplied, markApplied, type Application } from "@/lib/applications";
import { refreshResumeFacts } from "@/lib/jobs";
import type { NextStep } from "./nextSteps";

/**
 * The one thing to do now, with the next two named behind it.
 *
 * This is the piece doing the retention work. Today the dashboard hands you
 * eleven buttons and no opinion; a visit that ends in one finished thing is
 * worth more than a visit that ends in a scroll. The ranking lives in
 * ./nextSteps so the band and the status table cannot disagree.
 *
 * The queue line is visible on purpose rather than hidden behind the primary
 * action: naming what is next is what makes the choice feel like a choice
 * instead of a demand.
 */

type Props = {
  steps: NextStep[];
  /** For the actions the band performs itself rather than linking out. */
  applications: Application[];
  onChanged: (next: Application) => void;
  onRebuilt: () => void;
};

export default function NextActionBand({
  steps,
  applications,
  onChanged,
  onRebuilt,
}: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  if (steps.length === 0) {
    return (
      <section className="rr-card px-5 py-4" data-testid="dashboard-next">
        <p className="text-sm text-muted-foreground">{t("nextNothing")}</p>
      </section>
    );
  }

  const [first, ...rest] = steps;
  const app = first.jobId
    ? applications.find((a) => a.job_id === first.jobId)
    : undefined;

  async function run(op: () => Promise<Application | void>) {
    setBusy(true);
    setFailed(false);
    try {
      const next = await op();
      if (next) onChanged(next);
      else onRebuilt();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  function primary() {
    switch (first.kind) {
      case "send":
        return app ? (
          <a
            className="rr-btn"
            href={app.apply_url}
            rel="noopener noreferrer"
            target="_blank"
            // Fire and forget: the posting must open whether or not this lands.
            onClick={() => {
              markApplied(app.job_id).then(onChanged).catch(() => {});
            }}
          >
            {t("actionApply")}
          </a>
        ) : null;
      case "confirm":
        return app ? (
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <button
              type="button"
              className="rr-btn"
              disabled={busy}
              onClick={() => run(() => confirmApplied(app.job_id, true))}
            >
              {t("yes")}
            </button>
            <button
              type="button"
              className="rr-btn rr-btn-ghost"
              disabled={busy}
              onClick={() => run(() => confirmApplied(app.job_id, false))}
            >
              {t("no")}
            </button>
          </span>
        ) : null;
      case "rebuild":
        return (
          <button
            type="button"
            className="rr-btn"
            disabled={busy}
            // Spends credits, so it only ever runs from this press.
            onClick={() => run(async () => void (await refreshResumeFacts()))}
          >
            {busy ? t("rebuilding") : t("rebuildNow")}
          </button>
        );
      case "followUp":
        return app ? (
          <Link className="rr-btn" href={`/jobs/${encodeURIComponent(app.job_id)}`}>
            {t("actionLogResponse")}
          </Link>
        ) : null;
      default:
        return first.href ? (
          <Link className="rr-btn" href={first.href}>
            {first.kind === "tailor" ? t("digestTailor") : t("open")}
          </Link>
        ) : null;
    }
  }

  return (
    <section
      className="overflow-hidden rounded-lg border border-primary bg-sheet"
      data-testid="dashboard-next"
      aria-labelledby="dash-next"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-l-4 border-primary px-4 py-3.5">
        <div className="min-w-0 flex-[1_1_16rem]">
          <p
            id="dash-next"
            className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-primary"
          >
            {t("doThisFirst")}
          </p>
          <p className="mt-1 font-display text-[1.05rem] font-medium tracking-[-0.02em]">
            {t(first.titleKey as "nextSendTailored", first.params)}
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {t(first.whyKey as "nextSendTailoredWhy", first.params)}
          </p>
          {failed ? (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {tJobs("actionFailed")}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {first.kind === "confirm" ? (
            <span className="text-sm text-muted-foreground">{t("didYouApply")}</span>
          ) : null}
          {primary()}
          {app ? (
            <Link
              className="rr-btn rr-btn-ghost"
              href={`/jobs/${encodeURIComponent(app.job_id)}`}
            >
              {t("open")}
            </Link>
          ) : null}
        </div>
      </div>
      {rest.length > 0 ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-rule px-5 py-2.5 text-sm text-muted-foreground">
          <span>
            {t("nextThen")}{" "}
            <span className="font-semibold text-foreground">
              {t(rest[0].titleKey as "nextFollowUp", rest[0].params)}
            </span>
          </span>
          {rest.length > 1 ? (
            <Link href="/dashboard/applications" className="ml-auto underline">
              {t("nextMoreWaiting", { count: rest.length - 1 })}
            </Link>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
