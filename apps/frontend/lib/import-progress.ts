/**
 * Pure reducer turning one import-run snapshot (polled or streamed over SSE)
 * into the view model the progress card renders from. No React, no fetch,
 * no DOM — this is the only part of the import-progress feature the repo's
 * `node:test` harness can exercise directly.
 */

export type ImportStepStatus = "pending" | "active" | "done" | "skipped";

export type ImportSnapshot = {
  import_id: string;
  status: "running" | "done" | "failed";
  version: number;
  applied: number;
  steps: {
    key: string;
    label: string;
    status: ImportStepStatus;
    detail: string | null;
  }[];
  error: string | null;
  started_at: string;
  stale: boolean;
};

export type ImportViewStep = {
  key: string;
  label: string;
  status: ImportStepStatus;
  detail: string | null;
  unlabelled: boolean;
};

export type ImportView = {
  mode: "running" | "done" | "failed" | "stale";
  steps: ImportViewStep[];
  doneCount: number;
  totalCount: number;
  error: string | null;
  showPlan: boolean;
};

export function toImportView(snap: ImportSnapshot): ImportView {
  const mode = snap.status === "running" && snap.stale ? "stale" : snap.status;

  const steps: ImportViewStep[] = snap.steps.map((step) => ({
    key: step.key,
    label: step.label,
    status: step.status,
    detail: step.detail,
    unlabelled: step.label === "",
  }));

  const doneCount = steps.filter((step) => step.status === "done").length;

  return {
    mode,
    steps,
    doneCount,
    totalCount: steps.length,
    error: snap.error,
    showPlan: mode !== "done",
  };
}

export function importTurnStartsAt(snap: ImportSnapshot): number {
  return Date.parse(snap.started_at);
}
