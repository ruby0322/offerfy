import assert from "node:assert/strict";
import test from "node:test";

import {
  emptyTailorCursor,
  isTailorRunSnapshot,
  nextTailorCursor,
  planTailorAdopt,
  seedTailorCursor,
  shouldStartTailorRun,
  tailorBlocksSave,
  tailorRunBlocksComposer,
  tailorSourceChanged,
  tailorStepIndex,
  tailorTranscriptMoved,
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
    message_count: 2,
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
  assert.equal(toTailorRunView(snap({ stale: true, phase: "mapping" })).phase, "mapping");
});

test("a finished pass with no recorded stage says so", () => {
  const view = toTailorRunView(snap({ status: "done", phase: null }));
  assert.equal(view.mode, "done");
  assert.equal(view.phase, null);
});

test("a finished pass keeps the furthest stage it reached", () => {
  // The server stopped clearing `phase` on a terminal row precisely so this
  // is available: it is what tells four ticks from two ticks and two skips.
  const view = toTailorRunView(snap({ status: "done", phase: "mapping" }));
  assert.equal(view.phase, "mapping");
  assert.deepEqual(
    view.steps.map((step) => step.status),
    ["done", "done", "done", "skipped"],
  );
  assert.equal(view.doneCount, 3);
  assert.equal(view.totalCount, 4);
});

test("a live pass marks the stage it is in and leaves the rest pending", () => {
  const view = toTailorRunView(snap({ status: "running", phase: "read_job" }));
  assert.deepEqual(
    view.steps.map((step) => step.status),
    ["done", "active", "pending", "pending"],
  );
  assert.equal(view.doneCount, 1);
});

test("the step keys are the four stages, in order", () => {
  assert.deepEqual(
    toTailorRunView(snap()).steps.map((step) => step.key),
    ["read_resume", "read_job", "mapping", "proposing"],
  );
});

test("a pass that got all the way through ticks every step", () => {
  const view = toTailorRunView(snap({ status: "done", phase: "proposing" }));
  assert.equal(view.doneCount, 4);
  assert.ok(view.steps.every((step) => step.status === "done"));
});

test("the legacy phase names still place the cursor", () => {
  // Only finish/fail clear the column and a stale run keeps its phase
  // forever, so a pass that outlives a deploy still carries one of these.
  assert.equal(tailorStepIndex("reading"), 0);
  assert.equal(tailorStepIndex("assessing"), 2);
  // `writing` has no stage now -- the pass is forbidden to edit -- but a run
  // that got as far as editing had certainly read both documents.
  assert.equal(tailorStepIndex("writing"), 3);
  assert.equal(tailorStepIndex(null), null);
  assert.equal(tailorStepIndex("something else"), null);
});

test("an unplaceable phase claims nothing either way", () => {
  const live = toTailorRunView(snap({ status: "running", phase: null }));
  assert.ok(live.steps.every((step) => step.status === "pending"));
  assert.equal(live.doneCount, 0);
  const over = toTailorRunView(snap({ status: "done", phase: null }));
  assert.ok(over.steps.every((step) => step.status === "skipped"));
});

test("a stale pass shows the stage it died in as reached, not as active", () => {
  // Its runner is gone, so nothing is in progress -- but it did get there.
  const view = toTailorRunView(snap({ stale: true, phase: "mapping" }));
  assert.equal(view.mode, "stale");
  assert.deepEqual(
    view.steps.map((step) => step.status),
    ["done", "done", "done", "skipped"],
  );
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

test("a fresh cursor has adopted nothing and owes nothing", () => {
  const cursor = emptyTailorCursor();
  assert.equal(cursor.version, 0);
  assert.equal(cursor.messageCount, null);
  assert.equal(cursor.sourceHash, null);
  assert.equal(cursor.outstanding, false);
});

test("seeding takes the version and count but never the hash", () => {
  // The mount loaded the source itself rather than adopting it from a
  // snapshot, so claiming this snapshot's hash would claim a buffer the
  // editor may never have seen.
  const cursor = seedTailorCursor(snap({ version: 7, message_count: 4 }));
  assert.equal(cursor.version, 7);
  assert.equal(cursor.messageCount, 4);
  assert.equal(cursor.sourceHash, null);
});

test("the transcript is read by message_count, not by version", () => {
  // This is the whole reason the field exists: `touch()` bumps `version` on
  // every streamed chunk, so keyed on it the 0.4s poll would re-read the
  // entire transcript on every frame for the whole pass.
  const cursor = { ...emptyTailorCursor(), version: 3, messageCount: 5 };
  assert.equal(tailorTranscriptMoved(snap({ version: 99, message_count: 5 }), cursor), false);
  assert.equal(tailorTranscriptMoved(snap({ version: 3, message_count: 6 }), cursor), true);
});

test("with no count on the wire the version is the fallback", () => {
  // Missing a written row entirely is worse than reading too often, so an
  // API that predates the field still gets the fix, just coarsely.
  const cursor = { ...emptyTailorCursor(), version: 3, messageCount: null };
  assert.equal(tailorTranscriptMoved(snap({ version: 4, message_count: null }), cursor), true);
  assert.equal(tailorTranscriptMoved(snap({ version: 3, message_count: null }), cursor), false);
});

test("nothing adopted yet against a live pass adopts once", () => {
  const cursor = emptyTailorCursor();
  assert.equal(tailorSourceChanged(snap({ status: "running" }), cursor), true);
});

test("a matching hash is not a change, a moved one is", () => {
  const cursor = { ...emptyTailorCursor(), sourceHash: "abc" };
  assert.equal(tailorSourceChanged(snap({ source_hash: "abc" }), cursor), false);
  assert.equal(tailorSourceChanged(snap({ source_hash: "def" }), cursor), true);
});

test("with no hash on the wire only the terminal frame adopts", () => {
  // There is no `applied` counter to fall back on the way the import has, so
  // the cost is one redundant re-read per pass rather than a wrong answer.
  const cursor = emptyTailorCursor();
  assert.equal(tailorSourceChanged(snap({ source_hash: null, status: "running" }), cursor), false);
  assert.equal(tailorSourceChanged(snap({ source_hash: null, status: "done" }), cursor), true);
});

test("an outstanding adopt keeps asking, whatever the frame says", () => {
  // Without this one failed getResume is permanent: the next frame carries
  // the same hash, so nothing would ever retry.
  const cursor = { ...emptyTailorCursor(), sourceHash: "abc", outstanding: true };
  assert.equal(planTailorAdopt(snap({ source_hash: "abc" }), cursor).adoptSource, true);
});

test("a successful adopt records the hash; a failed one goes into debt", () => {
  const cursor = emptyTailorCursor();
  const adopted = nextTailorCursor(snap({ source_hash: "def" }), cursor, "adopted");
  assert.equal(adopted.sourceHash, "def");
  assert.equal(adopted.outstanding, false);
  const failed = nextTailorCursor(snap({ source_hash: "def" }), cursor, "failed");
  assert.equal(failed.sourceHash, null);
  assert.equal(failed.outstanding, true);
});

test("a frame that asked for nothing is not evidence about the buffer", () => {
  // Recording its hash would silence the very next frame that does carry a
  // change.
  const cursor = emptyTailorCursor();
  const next = nextTailorCursor(snap({ source_hash: "def" }), cursor, "not-needed");
  assert.equal(next.sourceHash, null);
});

test("the read cursor advances even when the adopt failed", () => {
  // A missed transcript re-read costs a stale message list until the next
  // frame; the resume is the thing that must not be lost.
  const cursor = { ...emptyTailorCursor(), version: 1, messageCount: 1 };
  const next = nextTailorCursor(snap({ version: 9, message_count: 4 }), cursor, "failed");
  assert.equal(next.version, 9);
  assert.equal(next.messageCount, 4);
});

test("a running pass forbids saving, and so does an unlanded adopt", () => {
  const clean = emptyTailorCursor();
  const owing = { ...clean, outstanding: true };
  assert.equal(tailorBlocksSave("running", clean), true);
  assert.equal(tailorBlocksSave("done", owing), true);
  assert.equal(tailorBlocksSave("done", clean), false);
  // A stale pass has no runner left, so the cursor alone decides -- blocking
  // on the mode would refuse to save for as long as the card is on screen.
  assert.equal(tailorBlocksSave("stale", clean), false);
  assert.equal(tailorBlocksSave(undefined, clean), false);
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
