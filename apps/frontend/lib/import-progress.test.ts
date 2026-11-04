import assert from "node:assert/strict";
import test from "node:test";

import type { ImportSnapshot, ImportStepStatus } from "./import-progress";
import { importSourceChanged, importTurnStartsAt, toImportView } from "./import-progress";

function step(
  key: string,
  status: ImportStepStatus,
  label = key,
  detail: string | null = null,
) {
  return { key, label, status, detail };
}

const d = (key: string) => step(key, "done");
const act = (key: string) => step(key, "active");
const pend = (key: string) => step(key, "pending");

function snap(overrides: Partial<ImportSnapshot> = {}): ImportSnapshot {
  return {
    import_id: "import-1",
    status: "running",
    version: 1,
    applied: 0,
    steps: [],
    error: null,
    started_at: "2026-11-04T00:00:00+00:00",
    stale: false,
    ...overrides,
  };
}

test("running snapshot counts done steps against the plan", () => {
  const v = toImportView(
    snap({ status: "running", steps: [d("a"), d("b"), act("c"), pend("d")] }),
  );
  assert.equal(v.mode, "running");
  assert.equal(v.doneCount, 2);
  assert.equal(v.totalCount, 4);
});

test("a stale running snapshot becomes stale mode", () => {
  assert.equal(toImportView(snap({ status: "running", stale: true })).mode, "stale");
});

test("a stale flag on a finished run is ignored", () => {
  assert.equal(toImportView(snap({ status: "done", stale: true })).mode, "done");
});

test("done collapses the plan", () => {
  assert.equal(toImportView(snap({ status: "done" })).showPlan, false);
});

test("failed keeps the plan and the error visible", () => {
  const v = toImportView(snap({ status: "failed", error: "ran out of turns" }));
  assert.equal(v.showPlan, true);
  assert.equal(v.error, "ran out of turns");
});

test("an empty plan still yields a usable view", () => {
  const v = toImportView(snap({ status: "running", steps: [] }));
  assert.equal(v.totalCount, 0);
  assert.equal(v.doneCount, 0);
});

test("a step with an empty label is marked unlabelled", () => {
  const v = toImportView(
    snap({
      status: "running",
      steps: [
        { key: "edit-1", label: "", status: "done", detail: null },
        { key: "work", label: "Work history", status: "active", detail: null },
      ],
    }),
  );
  assert.deepEqual(
    v.steps.map((s) => s.unlabelled),
    [true, false],
  );
});

test("running without staleness stays running and keeps showPlan true", () => {
  const v = toImportView(snap({ status: "running" }));
  assert.equal(v.mode, "running");
  assert.equal(v.showPlan, true);
});

test("skipped steps count neither as done nor against progress miscounted", () => {
  const v = toImportView(
    snap({ steps: [d("a"), step("b", "skipped"), act("c")] }),
  );
  assert.equal(v.doneCount, 1);
  assert.equal(v.totalCount, 3);
});

test("importTurnStartsAt parses the ISO timestamp", () => {
  const ms = importTurnStartsAt(snap({ started_at: "2026-11-04T12:00:00+00:00" }));
  assert.equal(ms, Date.parse("2026-11-04T12:00:00+00:00"));
});

test("steps preserve key, label, status and detail", () => {
  const v = toImportView(
    snap({ steps: [{ key: "k1", label: "Summary", status: "active", detail: "working" }] }),
  );
  assert.deepEqual(v.steps[0], {
    key: "k1",
    label: "Summary",
    status: "active",
    detail: "working",
    unlabelled: false,
  });
});

// --------------------------------------------------------------------------
// importSourceChanged -- the rule behind adopting the server's source
// --------------------------------------------------------------------------

test("a snapshot whose applied count grew means the source moved", () => {
  assert.equal(importSourceChanged(snap({ applied: 3 }), 2), true);
});

test("a snapshot that only bumped version leaves the source alone", () => {
  // A plan declaration bumps `version` without applying anything.
  assert.equal(importSourceChanged(snap({ version: 9, applied: 2 }), 2), false);
});

test("the terminal done snapshot always means the source moved", () => {
  // The run's last edit can land in the same write that ends the run, so
  // `done` must not depend on having seen `applied` grow.
  assert.equal(importSourceChanged(snap({ status: "done", applied: 2 }), 2), true);
});

test("a failed run that applied nothing new leaves the source alone", () => {
  assert.equal(importSourceChanged(snap({ status: "failed", applied: 2 }), 2), false);
});

test("a failed run that applied a section still means the source moved", () => {
  assert.equal(importSourceChanged(snap({ status: "failed", applied: 3 }), 2), true);
});
