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

/**
 * Whether this snapshot means the resume's source on the server has moved on
 * from the copy the editor is holding, so the editor must adopt the server's.
 *
 * `seenApplied` is the `applied` count of the last snapshot already accounted
 * for. A higher one means another section landed.
 *
 * `done` counts as changed on its own, whatever `applied` says. The run's last
 * edit can land in the same write that ends the run, and a stream that
 * reconnects or drops a frame can miss the growth entirely -- and the cost of
 * being wrong is asymmetric: one redundant fetch against an import that is
 * silently replaced by whatever stale source the editor still holds (see
 * `EditorShell`'s `onImportSnapshot`).
 */
export function importSourceChanged(snap: ImportSnapshot, seenApplied: number): boolean {
  return snap.applied > seenApplied || snap.status === "done";
}
