import assert from "node:assert/strict";
import test from "node:test";

import type {
  ImportAdoptCursor,
  ImportSnapshot,
  ImportStepStatus,
} from "./import-progress";
import {
  RENDER_CHECK_KEY,
  emptyImportCursor,
  importBlocksSave,
  importCardIndex,
  importErrorKey,
  importSourceChanged,
  importTurnStartsAt,
  nextImportCursor,
  planImportAdopt,
  seedImportCursor,
  toImportView,
} from "./import-progress";

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

// --------------------------------------------------------------------------
// The reserved render-check key -- takes precedence over `unlabelled`,
// because the backend writes it with an empty label on purpose (the
// translated text lives here, not on the server) and the generic
// "unnamed edit" wording would be wrong for it.
// --------------------------------------------------------------------------

test("the render-check step is flagged renderCheck, not unlabelled", () => {
  const v = toImportView(
    snap({
      status: "running",
      steps: [
        { key: RENDER_CHECK_KEY, label: "", status: "active", detail: null },
        { key: "edit-1", label: "", status: "done", detail: null },
      ],
    }),
  );
  assert.deepEqual(
    v.steps.map((s) => s.renderCheck),
    [true, false],
  );
  assert.deepEqual(
    v.steps.map((s) => s.unlabelled),
    [false, true],
  );
});

test("the render-check step reports repairing from its detail", () => {
  const checking = toImportView(
    snap({ steps: [{ key: RENDER_CHECK_KEY, label: "", status: "active", detail: null }] }),
  ).steps[0];
  const repairing = toImportView(
    snap({
      steps: [{ key: RENDER_CHECK_KEY, label: "", status: "active", detail: "repairing" }],
    }),
  ).steps[0];
  const done = toImportView(
    snap({ steps: [{ key: RENDER_CHECK_KEY, label: "", status: "done", detail: null }] }),
  ).steps[0];
  assert.equal(checking.renderCheck, true);
  assert.equal(checking.repairing, false);
  assert.equal(repairing.repairing, true);
  assert.equal(done.repairing, false);
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
    renderCheck: false,
    repairing: false,
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

// --------------------------------------------------------------------------
// importCardIndex -- the card is scoped to the import's own turn
// --------------------------------------------------------------------------

const TURN = Date.parse("2026-11-04T12:00:00+00:00");

function msg(role: string, iso: string) {
  return { role, created_at: iso };
}

test("the card sits last while the import is the latest turn", () => {
  const messages = [
    msg("tool", "2026-11-04T12:00:01+00:00"),
    msg("assistant", "2026-11-04T12:00:09+00:00"),
  ];
  assert.equal(importCardIndex(messages, TURN), 2);
});

test("the card sits above the next user turn, not below it", () => {
  const messages = [
    msg("tool", "2026-11-04T12:00:01+00:00"),
    msg("assistant", "2026-11-04T12:00:09+00:00"),
    msg("user", "2026-11-04T12:05:00+00:00"),
    msg("assistant", "2026-11-04T12:05:30+00:00"),
  ];
  assert.equal(importCardIndex(messages, TURN), 2);
});

test("a user turn before the import does not pull the card up", () => {
  const messages = [
    msg("user", "2026-11-04T11:00:00+00:00"),
    msg("assistant", "2026-11-04T11:00:20+00:00"),
    msg("tool", "2026-11-04T12:00:01+00:00"),
  ];
  assert.equal(importCardIndex(messages, TURN), 3);
});

test("an earlier chat turn and a later one bracket the card correctly", () => {
  const messages = [
    msg("user", "2026-11-04T11:00:00+00:00"),
    msg("tool", "2026-11-04T12:00:01+00:00"),
    msg("assistant", "2026-11-04T12:00:09+00:00"),
    msg("user", "2026-11-04T13:00:00+00:00"),
  ];
  assert.equal(importCardIndex(messages, TURN), 3);
});

test("an optimistically appended user message counts as the next turn", () => {
  // Sent from this client while the import was still running: it has no
  // server `created_at` yet, only the `timestamp` the shell gave it.
  const messages = [
    msg("tool", "2026-11-04T12:00:01+00:00"),
    { role: "user", timestamp: "2026-11-04T12:00:30+00:00" },
  ];
  assert.equal(importCardIndex(messages, TURN), 1);
});

test("messages without any timestamp leave the card last", () => {
  const messages = [{ role: "user" }, { role: "assistant" }];
  assert.equal(importCardIndex(messages, TURN), 2);
});

test("an unparseable turn start leaves the card last", () => {
  const messages = [msg("user", "2026-11-04T13:00:00+00:00")];
  assert.equal(importCardIndex(messages, Number.NaN), 1);
});

test("an empty transcript puts the card at index zero", () => {
  assert.equal(importCardIndex([], TURN), 0);
});

test("a user message exactly at the turn start is the next turn", () => {
  // The import writes no user row, so a `user` row sharing the run's own
  // timestamp cannot belong to the import.
  assert.equal(importCardIndex([msg("user", "2026-11-04T12:00:00+00:00")], TURN), 0);
});

// --------------------------------------------------------------------------
// importErrorKey -- no raw backend text in a trilingual card
// --------------------------------------------------------------------------

test("no error means no key", () => {
  assert.equal(importErrorKey(null), null);
  assert.equal(importErrorKey(""), null);
});

test("each known code maps to its own key", () => {
  assert.equal(importErrorKey("upload_missing"), "importErrorUploadMissing");
  assert.equal(importErrorKey("unexpected"), "importErrorUnexpected");
  assert.equal(importErrorKey("render_check_failed"), "importErrorRenderCheckFailed");
});

test("an unrecognised code falls back rather than being shown as text", () => {
  // The leak this replaces: a raw message, untranslated, naming an internal
  // storage key.
  assert.equal(
    importErrorKey("could not read the uploaded file ('uploads/a/b/c.pdf')"),
    "importErrorUnexpected",
  );
  assert.equal(importErrorKey("superseded: heartbeat went stale"), "importErrorUnexpected");
});

// --------------------------------------------------------------------------
// the adopt cursor -- a failed adopt must retry, and must block every save
// --------------------------------------------------------------------------

const fresh = emptyImportCursor();

test("a fresh cursor has taken in nothing and owes nothing", () => {
  assert.deepEqual(fresh, { applied: 0, version: 0, outstanding: false });
});

test("a seeded cursor accounts for the snapshot the mount already loaded", () => {
  assert.deepEqual(seedImportCursor(snap({ applied: 2, version: 7 })), {
    applied: 2,
    version: 7,
    outstanding: false,
  });
});

test("a plan-declared frame asks for a transcript read but no adopt", () => {
  // `applied` has not moved: the model named its sections, it has not
  // rewritten anything yet.
  assert.deepEqual(planImportAdopt(snap({ applied: 0, version: 1 }), fresh), {
    adoptSource: false,
    readTranscript: true,
  });
});

test("a section landing asks for both", () => {
  assert.deepEqual(planImportAdopt(snap({ applied: 1, version: 2 }), fresh), {
    adoptSource: true,
    readTranscript: true,
  });
});

test("the terminal frame asks for an adopt even with applied unchanged", () => {
  const seen: ImportAdoptCursor = { applied: 2, version: 5, outstanding: false };
  assert.equal(
    planImportAdopt(snap({ status: "done", applied: 2, version: 5 }), seen).adoptSource,
    true,
  );
});

test("a failed adopt is retried by the next snapshot, unchanged though it is", () => {
  // THE regression this cursor exists for. One transient `getResume` failure
  // used to be permanent: the refs had already advanced, so the next frame
  // carrying the same `applied` decided nothing had changed and the editor
  // served the pre-import source to every save path for the rest of the
  // session.
  const frame = snap({ applied: 1, version: 2 });
  const afterFailure = nextImportCursor(frame, fresh, "failed");
  assert.equal(afterFailure.outstanding, true);
  assert.equal(afterFailure.applied, 0, "a failed adopt must not advance applied");
  assert.equal(planImportAdopt(frame, afterFailure).adoptSource, true);
  // And the retry is what clears it.
  const afterRetry = nextImportCursor(frame, afterFailure, "adopted");
  assert.deepEqual(afterRetry, { applied: 1, version: 2, outstanding: false });
});

test("an outstanding adopt survives a frame that asks for nothing of its own", () => {
  const owing: ImportAdoptCursor = { applied: 0, version: 2, outstanding: true };
  const quiet = snap({ applied: 0, version: 2 });
  assert.deepEqual(planImportAdopt(quiet, owing), {
    adoptSource: true,
    readTranscript: false,
  });
});

test("a successful adopt advances applied and clears the debt", () => {
  const owing: ImportAdoptCursor = { applied: 0, version: 2, outstanding: true };
  assert.deepEqual(nextImportCursor(snap({ applied: 3, version: 4 }), owing, "adopted"), {
    applied: 3,
    version: 4,
    outstanding: false,
  });
});

test("version advances whatever the adopt did", () => {
  // A missed transcript read costs a stale message list until the next frame
  // or a reload. It is not the resume, so it does not hold the cursor back.
  assert.equal(nextImportCursor(snap({ version: 9 }), fresh, "failed").version, 9);
  assert.equal(nextImportCursor(snap({ version: 9 }), fresh, "not-needed").version, 9);
});

test("neither counter ever moves backwards", () => {
  const ahead: ImportAdoptCursor = { applied: 5, version: 9, outstanding: false };
  const behind = snap({ applied: 2, version: 3 });
  assert.deepEqual(nextImportCursor(behind, ahead, "adopted"), ahead);
  assert.deepEqual(nextImportCursor(behind, ahead, "not-needed"), ahead);
});

test("a live run blocks every save path", () => {
  assert.equal(importBlocksSave("running", fresh), true);
});

test("an outstanding adopt blocks every save path after the run is over", () => {
  // Finding 3's window: `mode` leaves "running" on the terminal frame, which
  // is at least one round trip before that frame's source has been adopted.
  const owing: ImportAdoptCursor = { applied: 1, version: 3, outstanding: true };
  assert.equal(importBlocksSave("done", owing), true);
  assert.equal(importBlocksSave("failed", owing), true);
  assert.equal(importBlocksSave("stale", owing), true);
  assert.equal(importBlocksSave(undefined, owing), true);
});

test("a settled cursor lets a finished, failed or stale run save again", () => {
  // The import is over and our buffer matches the server. Refusing here would
  // trade a clobber for a different trap: an editor that never saves again.
  assert.equal(importBlocksSave("done", fresh), false);
  assert.equal(importBlocksSave("failed", fresh), false);
  assert.equal(importBlocksSave("stale", fresh), false);
  assert.equal(importBlocksSave(undefined, fresh), false);
});
