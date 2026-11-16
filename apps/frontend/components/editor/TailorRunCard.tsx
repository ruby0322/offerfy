"use client";

import { useTranslations } from "next-intl";
import { AlertTriangle, Check, Circle, ExternalLink, Loader2, Minus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { cn } from "@/lib/utils";
import type { TailorRunMode, TailorRunView, TailorViewStep } from "@/lib/tailor-run";

type Props = {
  view: TailorRunView;
  /** A Run-again click is still in flight. The button goes inert so a second
   *  click cannot start a second pass (the backend 409s it, which is correct
   *  and useless to look at). */
  actionPending?: boolean;
  onRetry: () => void;
};

/** Lifted from `ImportProgressCard`, so the two runs read the same. */
function StepMark({ status }: { status: TailorViewStep["status"] }) {
  const className = "size-3.5 shrink-0";
  if (status === "done") return <Check className={cn(className, "text-success")} aria-hidden="true" />;
  if (status === "active") {
    return <Loader2 className={cn(className, "animate-spin text-clay")} aria-hidden="true" />;
  }
  if (status === "skipped") {
    return <Minus className={cn(className, "text-muted-foreground")} aria-hidden="true" />;
  }
  return <Circle className={cn(className, "text-muted-foreground")} aria-hidden="true" />;
}

function StepRow({ step, label }: { step: TailorViewStep; label: string }) {
  return (
    <div
      data-testid={`tailor-step-${step.key}`}
      className={cn(
        "flex items-start gap-2 text-xs",
        step.status === "pending" && "text-muted-foreground",
        step.status === "active" && "font-medium text-clay",
        step.status === "done" && "text-success",
        step.status === "skipped" && "text-muted-foreground line-through",
      )}
    >
      <span className="mt-0.5">
        <StepMark status={step.status} />
      </span>
      {/* No fixed width anywhere on this row: the labels are written in
          zh-TW first and CJK runs about 1.5x wider than the English. */}
      <span className="min-w-0 flex-1">{label}</span>
    </div>
  );
}

function HeaderIcon({ mode }: { mode: TailorRunMode }) {
  const className = "size-3.5 shrink-0";
  if (mode === "running") {
    return <Loader2 className={cn(className, "animate-spin text-clay")} aria-hidden="true" />;
  }
  if (mode === "done") return <Check className={cn(className, "text-success")} aria-hidden="true" />;
  if (mode === "stale") {
    return <AlertTriangle className={cn(className, "text-amber-600")} aria-hidden="true" />;
  }
  return <X className={cn(className, "text-destructive")} aria-hidden="true" />;
}

/**
 * The card that opens a tailor's conversation, in place of the canned message
 * the browser used to send as the person's own words.
 *
 * It is what the posting looks like inside the editor: the employer's mark,
 * the role, and a link back to the Offerfy page for it. That link matters
 * beyond decoration -- it is the only way back to the posting from here, and
 * the apply button, the save action and the full description all live on it.
 *
 * `aria-live="polite"` for the same reason the import card has it: the phase
 * changes under the person without any action of theirs.
 */
export default function TailorRunCard({ view, actionPending, onRetry }: Props) {
  const t = useTranslations("editor");
  const { mode, job, jobId, steps, doneCount, totalCount } = view;

  // One running title, with the step list below saying where it is. The three
  // phase-specific titles it replaces were a worse version of the same thing:
  // one line that changed twice in a 60-90 second pass, and `assessing` fired
  // on any narration so it rarely meant what it said.
  const title =
    mode === "running"
      ? t("tailorRunWorking")
      : mode === "done"
        ? t("tailorRunDone")
        : mode === "stale"
          ? t("tailorRunStale")
          : t("tailorRunFailed");

  const stepLabels: Record<TailorViewStep["key"], string> = {
    read_resume: t("tailorStepReadResume"),
    read_job: t("tailorStepReadJob"),
    mapping: t("tailorStepMapping"),
    proposing: t("tailorStepProposing"),
  };

  return (
    <div className="flex justify-start">
      <div
        data-testid="tailor-run-card"
        aria-live="polite"
        className={cn(
          "w-full max-w-[90vw] rounded-lg border px-3 py-3 text-sm",
          mode === "failed" ? "border-destructive/40 bg-destructive/5" : "",
          mode === "stale" ? "border-amber-500/50 bg-amber-500/5" : "",
          mode !== "failed" && mode !== "stale" ? "border-border bg-sheet" : "",
        )}
      >
        <div className="flex items-baseline gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-paper">
            <HeaderIcon mode={mode} />
          </span>
          <span className="min-w-0 flex-1 font-medium tracking-tight text-foreground">{title}</span>
          {/* A counter, not a progress bar: four steps is few enough that the
              list itself is the progress, and the number is for the glance. */}
          {mode === "running" ? (
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {doneCount} / {totalCount}
            </span>
          ) : null}
        </div>

        {/* The posting itself. `job` is absent for a tailor made from the
            editor's own dialog, which has a target_label and no posting at
            all -- the card then carries only its status, never an employer
            row invented from the resume title. */}
        {job ? (
          <div className="mt-2 pl-8">
            {jobId ? (
              <Link
                href={`/jobs/${jobId}`}
                className="group flex items-center gap-2.5 rounded-md border border-rule px-2.5 py-2 transition-colors hover:bg-paper"
              >
                <EmployerLogo company={job.company} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-display text-sm text-foreground">
                    {job.title}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {job.company}
                    {job.location ? ` · ${job.location}` : null}
                    {/* Reported, not hidden: a posting can close while the
                        tailor for it lives on, and the person should not have
                        to click through to find that out. */}
                    {job.is_active ? null : ` · ${t("tailorRunJobClosed")}`}
                  </span>
                </span>
                <ExternalLink
                  className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground"
                  aria-hidden="true"
                />
              </Link>
            ) : (
              <div className="flex items-center gap-2.5 px-2.5 py-2">
                <EmployerLogo company={job.company} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-display text-sm text-foreground">
                    {job.title}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">{job.company}</span>
                </span>
              </div>
            )}
          </div>
        ) : null}

        {/* The four stages. Shown once the pass is over as well as during
            it: after it, the ticks and the struck-through skips are how the
            person tells a pass that did all four from one that stopped
            early -- which is the honest explanation when the suggestions look
            thin. */}
        <div className="mt-3 flex flex-col gap-1.5 pl-8">
          {steps.map((step) => (
            <StepRow key={step.key} step={step} label={stepLabels[step.key]} />
          ))}
        </div>

        {mode === "stale" ? (
          <p className="mt-2 pl-8 text-xs text-muted-foreground">{t("tailorRunStaleHint")}</p>
        ) : null}
        {mode === "failed" ? (
          <p className="mt-2 pl-8 text-xs text-muted-foreground">{t("tailorRunFailedHint")}</p>
        ) : null}

        {mode === "failed" || mode === "stale" ? (
          <div className="mt-2.5 flex items-center pl-8">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="ml-auto h-7 px-2.5 text-xs"
              data-testid="tailor-run-retry"
              disabled={actionPending}
              onClick={onRetry}
            >
              {t("tailorRunRetry")}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
