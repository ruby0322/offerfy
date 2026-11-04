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
    source_hash: "h-start",
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

/** A cursor holding `hash`, as one successful adopt leaves it. */
function holding(hash: string, applied = 2): ImportAdoptCursor {
  return { applied, version: 5, sourceHash: hash, outstanding: false };
}

/** A cursor that has adopted nothing, so it has no hash to compare. */
const nothingAdopted: ImportAdoptCursor = {
  applied: 2,
  version: 5,
  sourceHash: null,
  outstanding: false,
};

test("a snapshot whose source hash differs means the source moved", () => {
  assert.equal(importSourceChanged(snap({ source_hash: "h2" }), holding("h1")), true);
});

test("a snapshot whose source hash matches leaves the source alone", () => {
  // A plan declaration, a usage frame, a heartbeat: version moves, the
  // resume does not.
  assert.equal(
    importSourceChanged(snap({ version: 9, source_hash: "h1" }), holding("h1")),
    false,
  );
});

test("an edit reusing a done step key still means the source moved", () => {
  // THE regression this hash exists for. The agent's second edit to a
  // section it already marked done leaves `applied` untouched -- the
  // server's `advance()` is a no-op on an already-`done` key -- so the old
  // rule read this frame as "nothing happened" and the preview stayed one
  // edit behind for the rest of the run.
  assert.equal(
    importSourceChanged(snap({ applied: 2, source_hash: "h2" }), holding("h1")),
    true,
  );
});

test("a counter that moved without the source does not trigger an adopt", () => {
  // The other half: `advance()` commits `applied` on the edit's tool event,
  // one commit before the runner writes the source. A frame sampled in
  // that window reports the new count against the old source, and adopting
  // it used to leave the editor stuck exactly one edit behind -- `applied`
  // never returns to the same value, so nothing retried.
  assert.equal(
    importSourceChanged(snap({ applied: 3, source_hash: "h1" }), holding("h1")),
    false,
  );
});

test("the terminal done snapshot does not re-fetch a source already held", () => {
  // `done` forces an adopt only in the fallback below, where a dropped
  // frame would otherwise be missed forever. With a hash to compare, a
  // terminal frame that matches is genuinely nothing to do.
  assert.equal(
    importSourceChanged(snap({ status: "done", source_hash: "h1" }), holding("h1")),
    false,
  );
  assert.equal(
    importSourceChanged(snap({ status: "done", source_hash: "h2" }), holding("h1")),
    true,
  );
});

// The fallback: no hash on the cursor (nothing adopted yet) or none on the
// wire (an API older than the field). Both keep the pre-hash `applied` rule.

test("a live run adopts once to learn the hash it is holding", () => {
  // A reload mid-import: the editor loaded a source but cannot hash it, and
  // the run is still rewriting the resume. Waiting for `applied` here is
  // what left a reload during a repair phase on the old rule for good --
  // those edits all reuse keys the agent already marked done.
  const frame = snap({ applied: 2, source_hash: "h1", status: "running" });
  assert.equal(importSourceChanged(frame, nothingAdopted), true);
  // And it is paid once: the adopt seeds the hash, so the next frame is
  // compared against it rather than adopted again.
  const after = nextImportCursor(frame, nothingAdopted, "adopted");
  assert.equal(after.sourceHash, "h1");
  assert.equal(importSourceChanged(frame, after), false);
});

test("a finished run is not re-fetched just because no hash was adopted", () => {
  // Every editor open on a resume that was once imported hits this. The
  // mount has already loaded the source itself; a run that is over is not
  // rewriting anything.
  const settled = snap({ applied: 2, source_hash: "h1", status: "failed" });
  assert.equal(importSourceChanged(settled, nothingAdopted), false);
});

test("with nothing adopted yet, a grown applied count still means the source moved", () => {
  assert.equal(
    importSourceChanged(snap({ applied: 3, source_hash: undefined }), nothingAdopted),
    true,
  );
  assert.equal(
    importSourceChanged(snap({ applied: 2, source_hash: undefined }), nothingAdopted),
    false,
  );
});

test("an API that sends no source hash falls back to the applied rule", () => {
  const seen = holding("h1");
  assert.equal(importSourceChanged(snap({ source_hash: undefined, applied: 3 }), seen), true);
  assert.equal(importSourceChanged(snap({ source_hash: null, applied: 2 }), seen), false);
});

test("the terminal done snapshot always means the source moved, without a hash", () => {
  // The run's last edit can land in the same write that ends the run, so
  // `done` must not depend on having seen `applied` grow.
  assert.equal(
    importSourceChanged(snap({ status: "done", applied: 2, source_hash: null }), nothingAdopted),
    true,
  );
});

test("a failed run that applied nothing new leaves the source alone", () => {
  assert.equal(
    importSourceChanged(snap({ status: "failed", applied: 2, source_hash: null }), nothingAdopted),
    false,
  );
});

test("a failed run that applied a section still means the source moved", () => {
  assert.equal(
    importSourceChanged(snap({ status: "failed", applied: 3, source_hash: null }), nothingAdopted),
    true,
  );
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
  assert.deepEqual(fresh, { applied: 0, version: 0, sourceHash: null, outstanding: false });
});

test("a seeded cursor accounts for the snapshot the mount already loaded", () => {
  // No hash: the mount read the source itself rather than adopting it from
  // this snapshot, and claiming the snapshot's hash would claim a buffer it
  // may never have seen. `applied` carries it until the first real adopt.
  assert.deepEqual(seedImportCursor(snap({ applied: 2, version: 7, source_hash: "h9" })), {
    applied: 2,
    version: 7,
    sourceHash: null,
    outstanding: false,
  });
});

test("a plan-declared frame asks for a transcript read but no adopt", () => {
  // The model named its sections; it has not rewritten anything yet, so the
  // hash the editor is holding is still the one this frame reports.
  assert.deepEqual(
    planImportAdopt(snap({ applied: 0, version: 1, source_hash: "h1" }), {
      ...fresh,
      sourceHash: "h1",
    }),
    { adoptSource: false, readTranscript: true },
  );
  // Same frame from an API that sends no hash, against a cursor that has
  // adopted nothing: the `applied` fallback says the same thing.
  assert.deepEqual(
    planImportAdopt(snap({ applied: 0, version: 1, source_hash: undefined }), fresh),
    { adoptSource: false, readTranscript: true },
  );
});

test("a section landing asks for both", () => {
  assert.deepEqual(planImportAdopt(snap({ applied: 1, version: 2 }), fresh), {
    adoptSource: true,
    readTranscript: true,
  });
});

test("the terminal frame asks for an adopt even with applied unchanged", () => {
  // The fallback's rule, which is what a run whose edits all landed before
  // this client adopted anything still relies on.
  assert.equal(
    planImportAdopt(snap({ status: "done", applied: 2, version: 5 }), nothingAdopted)
      .adoptSource,
    true,
  );
});

test("a failed adopt is retried by the next snapshot, unchanged though it is", () => {
  // THE regression this cursor exists for. One transient `getResume` failure
  // used to be permanent: the refs had already advanced, so the next frame
  // carrying the same `applied` decided nothing had changed and the editor
  // served the pre-import source to every save path for the rest of the
  // session.
  const frame = snap({ applied: 1, version: 2, source_hash: "h1" });
  const afterFailure = nextImportCursor(frame, fresh, "failed");
  assert.equal(afterFailure.outstanding, true);
  assert.equal(afterFailure.applied, 0, "a failed adopt must not advance applied");
  assert.equal(afterFailure.sourceHash, null, "a failed adopt must not claim a hash");
  assert.equal(planImportAdopt(frame, afterFailure).adoptSource, true);
  // And the retry is what clears it.
  const afterRetry = nextImportCursor(frame, afterFailure, "adopted");
  assert.deepEqual(afterRetry, {
    applied: 1,
    version: 2,
    sourceHash: "h1",
    outstanding: false,
  });
});

test("an outstanding adopt survives a frame that asks for nothing of its own", () => {
  const owing: ImportAdoptCursor = {
    applied: 0,
    version: 2,
    sourceHash: "h1",
    outstanding: true,
  };
  const quiet = snap({ applied: 0, version: 2, source_hash: "h1" });
  assert.deepEqual(planImportAdopt(quiet, owing), {
    adoptSource: true,
    readTranscript: false,
  });
});

test("a successful adopt advances applied and clears the debt", () => {
  const owing: ImportAdoptCursor = {
    applied: 0,
    version: 2,
    sourceHash: null,
    outstanding: true,
  };
  assert.deepEqual(
    nextImportCursor(snap({ applied: 3, version: 4, source_hash: "h4" }), owing, "adopted"),
    { applied: 3, version: 4, sourceHash: "h4", outstanding: false },
  );
});

test("only an adopted frame sets the hash the next comparison trusts", () => {
  // A frame this editor decided it had nothing to do about says nothing
  // about what the editor is holding. Recording its hash would silence the
  // very next frame that does carry a change.
  const seen = holding("h1");
  const quiet = snap({ source_hash: "h2", applied: 2 });
  assert.equal(nextImportCursor(quiet, seen, "not-needed").sourceHash, "h1");
  assert.equal(nextImportCursor(quiet, seen, "adopted").sourceHash, "h2");
});

test("version advances whatever the adopt did", () => {
  // A missed transcript read costs a stale message list until the next frame
  // or a reload. It is not the resume, so it does not hold the cursor back.
  assert.equal(nextImportCursor(snap({ version: 9 }), fresh, "failed").version, 9);
  assert.equal(nextImportCursor(snap({ version: 9 }), fresh, "not-needed").version, 9);
});

test("neither counter ever moves backwards", () => {
  const ahead: ImportAdoptCursor = {
    applied: 5,
    version: 9,
    sourceHash: "h-ahead",
    outstanding: false,
  };
  const behind = snap({ applied: 2, version: 3, source_hash: "h-ahead" });
  assert.deepEqual(nextImportCursor(behind, ahead, "adopted"), ahead);
  assert.deepEqual(nextImportCursor(behind, ahead, "not-needed"), ahead);
});

test("a live run blocks every save path", () => {
  assert.equal(importBlocksSave("running", fresh), true);
});

test("an outstanding adopt blocks every save path after the run is over", () => {
  // Finding 3's window: `mode` leaves "running" on the terminal frame, which
  // is at least one round trip before that frame's source has been adopted.
  const owing: ImportAdoptCursor = {
    applied: 1,
    version: 3,
    sourceHash: null,
    outstanding: true,
  };
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
