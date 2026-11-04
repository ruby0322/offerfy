"use client";

import { useTranslations } from "next-intl";
import { AlertTriangle, Check, Circle, Loader2, Minus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ImportView, ImportViewStep } from "@/lib/import-progress";

type Props = {
  view: ImportView;
  /** Seconds since the import turn started. Owned by the caller, which polls
   *  a clock rather than this component doing its own timer. */
  elapsedSeconds: number;
  onRetry: () => void;
  onResume: () => void;
};

function StepMark({ status }: { status: ImportViewStep["status"] }) {
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

function StepRow({ step, unlabelledText }: { step: ImportViewStep; unlabelledText: string }) {
  const label = step.unlabelled ? unlabelledText : step.label;
  return (
    <div
      data-testid={`import-step-${step.key}`}
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
      <span className="min-w-0 flex-1">
        {label}
        {step.status === "done" && step.detail ? (
          <span className="font-normal text-muted-foreground"> — {step.detail}</span>
        ) : null}
      </span>
    </div>
  );
}

function HeaderIcon({ mode }: { mode: ImportView["mode"] }) {
  const className = "size-3.5";
  if (mode === "done") return <Check className={cn(className, "text-success")} aria-hidden="true" />;
  if (mode === "failed") return <X className={cn(className, "text-destructive")} aria-hidden="true" />;
  if (mode === "stale") {
    return <AlertTriangle className={cn(className, "text-amber-600")} aria-hidden="true" />;
  }
  return <Loader2 className={cn(className, "animate-spin text-clay")} aria-hidden="true" />;
}

/**
 * The import turn's own card: a plan filling in section by section, pinned
 * to the bottom of the chat turn so tool messages keep appending above it.
 * Mounted by `EditorShell`, which owns the snapshot and the clock.
 */
export default function ImportProgressCard({ view, elapsedSeconds, onRetry, onResume }: Props) {
  const t = useTranslations("editor");
  const { mode, steps, doneCount, totalCount, error, showPlan } = view;

  const title =
    mode === "done"
      ? t("importDoneTitle", { count: doneCount })
      : mode === "failed"
        ? t("importFailedTitle")
        : mode === "stale"
          ? t("importStaleTitle")
          : t("importTitle");

  const subtitle =
    mode === "failed" || mode === "stale"
      ? t("importNothingLost", { count: doneCount })
      : mode === "running"
        ? totalCount > 0
          ? t("importPlanFound", { total: totalCount })
          : t("importWorking")
        : null;

  const showBar = mode === "running" && totalCount > 0;
  const showSteps = showPlan && steps.length > 0;
  const progressPct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  return (
    <div className="flex w-full justify-start">
      <div
        data-testid="import-progress-card"
        data-mode={mode}
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
          {mode === "running" && totalCount > 0 ? (
            <span
              data-testid="import-step-count"
              className="shrink-0 text-xs tabular-nums text-muted-foreground"
            >
              {doneCount} / {totalCount}
            </span>
          ) : null}
        </div>

        {error ? (
          <p className="mt-2 rounded-md bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {subtitle ? <p className="mt-1 pl-8 text-xs text-muted-foreground">{subtitle}</p> : null}

        {showSteps ? (
          <div className="mt-2.5 space-y-1.5 pl-8">
            {steps.map((step) => (
              <StepRow key={step.key} step={step} unlabelledText={t("importUnlabelledStep")} />
            ))}
          </div>
        ) : null}

        {showBar ? (
          <div className="mt-2.5 h-[3px] overflow-hidden rounded-full bg-muted">
            <div
              className="h-[3px] rounded-full bg-clay transition-[width] duration-500 ease-out"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        ) : null}

        <div className="mt-2.5 flex items-center gap-2 pl-8 text-xs text-muted-foreground">
          {mode === "running" || mode === "done" ? (
            <span>{t("importElapsed", { seconds: elapsedSeconds })}</span>
          ) : null}
          {mode === "failed" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="ml-auto h-7 px-2.5 text-xs"
              data-testid="import-action-retry"
              onClick={onRetry}
            >
              {t("importRetry")}
            </Button>
          ) : null}
          {mode === "stale" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="ml-auto h-7 px-2.5 text-xs"
              data-testid="import-action-resume"
              onClick={onResume}
            >
              {t("importResume")}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
