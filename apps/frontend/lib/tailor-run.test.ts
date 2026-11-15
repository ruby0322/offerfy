import assert from "node:assert/strict";
import test from "node:test";

import {
  isTailorRunSnapshot,
  shouldStartTailorRun,
  tailorRunBlocksComposer,
  toTailorRunView,
  type TailorRunSnapshot,
} from "./tailor-run";

function snap(over: Partial<TailorRunSnapshot> = {}): TailorRunSnapshot {
  return {
    run_id: "r1",
    status: "running",
    phase: "reading",
    error: null,
    version: 3,
    stale: false,
    created_at: "2026-11-15T04:00:00+00:00",
    job_id: "j1",
    job: {
      job_id: "j1",
      title: "Payments Engineer",
      company: "Stripe",
      location: "Taipei",
      is_active: true,
    },
    source_hash: "abc",
    ...over,
  };
}

test("a live pass reads as running and keeps its phase", () => {
  const view = toTailorRunView(snap({ phase: "assessing" }));
  assert.equal(view.mode, "running");
  assert.equal(view.phase, "assessing");
});

test("stale outranks running, because a dead runner's row still says running", () => {
  // Nothing reaps a run orphaned by an API restart, so `status` alone would
  // spin the card forever.
  const view = toTailorRunView(snap({ status: "running", stale: true }));
  assert.equal(view.mode, "stale");
});

test("a stale pass keeps the phase it died in", () => {
  // It is the only true thing there is to say about it.
  assert.equal(toTailorRunView(snap({ stale: true, phase: "writing" })).phase, "writing");
});

test("a finished pass has no current phase", () => {
  const view = toTailorRunView(snap({ status: "done", phase: null }));
  assert.equal(view.mode, "done");
  assert.equal(view.phase, null);
});

test("a finished pass drops a phase the server left behind", () => {
  // finish() nulls it server-side; the view must not resurrect it if a stale
  // frame arrives out of order.
  assert.equal(toTailorRunView(snap({ status: "done", phase: "writing" })).phase, null);
});

test("a failed pass carries its reason", () => {
  const view = toTailorRunView(snap({ status: "failed", error: "provider exploded" }));
  assert.equal(view.mode, "failed");
  assert.equal(view.error, "provider exploded");
});

test("the posting travels to the card", () => {
  const view = toTailorRunView(snap());
  assert.equal(view.job?.company, "Stripe");
  assert.equal(view.jobId, "j1");
});

test("a tailor with no posting still has a card, just no employer row", () => {
  // The editor's own dialog makes these: a target_label and no job at all.
  const view = toTailorRunView(snap({ job: null, job_id: null }));
  assert.equal(view.job, null);
  assert.equal(view.jobId, null);
  assert.equal(view.mode, "running");
});

test("only a running pass shuts the composer", () => {
  // A typed turn during the pass is two agents writing one resume. A stale
  // pass has no runner left, so there is nothing to collide with.
  assert.equal(tailorRunBlocksComposer(toTailorRunView(snap())), true);
  assert.equal(tailorRunBlocksComposer(toTailorRunView(snap({ stale: true }))), false);
  assert.equal(tailorRunBlocksComposer(toTailorRunView(snap({ status: "done" }))), false);
  assert.equal(tailorRunBlocksComposer(toTailorRunView(snap({ status: "failed" }))), false);
  assert.equal(tailorRunBlocksComposer(undefined), false);
});

test("a pass starts only for a loaded tailor with no run and no messages", () => {
  const base = { kind: "tailor", loaded: true, messageCount: 0, snapshot: null };
  assert.equal(shouldStartTailorRun(base), true);
  assert.equal(shouldStartTailorRun({ ...base, kind: "master" }), false);
  assert.equal(shouldStartTailorRun({ ...base, loaded: false }), false);
  assert.equal(shouldStartTailorRun({ ...base, kind: undefined }), false);
});

test("an existing run of any status stops another from starting", () => {
  // This is the fix for the old bug: the browser's only guard was "are there
  // messages", which the orphaned user message satisfied by itself, so a
  // reload mid-pass never retried and never explained itself. The run row is
  // the durable answer now.
  const base = { kind: "tailor", loaded: true, messageCount: 0 };
  for (const status of ["running", "done", "failed"] as const) {
    assert.equal(shouldStartTailorRun({ ...base, snapshot: snap({ status }) }), false, status);
  }
});

test("a tailor someone has already talked in does not get a pass", () => {
  const base = { kind: "tailor", loaded: true, snapshot: null };
  assert.equal(shouldStartTailorRun({ ...base, messageCount: 4 }), false);
});

test("the snapshot guard rejects anything that is not one", () => {
  assert.equal(isTailorRunSnapshot(snap()), true);
  assert.equal(isTailorRunSnapshot(null), false);
  assert.equal(isTailorRunSnapshot({}), false);
  assert.equal(isTailorRunSnapshot({ run_id: "r1", status: "running" }), false);
  // An import snapshot must not be mistaken for one: the two streams have the
  // same `data:` convention and both carry version/status.
  assert.equal(
    isTailorRunSnapshot({ status: "running", version: 1, steps: [], applied: 0 }),
    false,
  );
});
