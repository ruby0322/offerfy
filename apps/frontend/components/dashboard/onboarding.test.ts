import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_POLLS,
  addValue,
  canSave,
  cardVisible,
  firstRunNotice,
  pollVerdict,
  removeValue,
} from "./onboarding";

const READY = { has_master: true, published: true };

test("the card shows only before a first save, and only with a resume to seed from", () => {
  assert.equal(cardVisible({ updated_at: null }, READY), true);
  // Saved once: the full editor is the place to change it from here on.
  assert.equal(cardVisible({ updated_at: "2026-11-19T00:00:00Z" }, READY), false);
  // No readable resume: the seed would be empty and the first run refused.
  assert.equal(cardVisible({ updated_at: null }, { has_master: false, published: false }), false);
  assert.equal(cardVisible({ updated_at: null }, { has_master: true, published: false }), false);
});

test("polling waits while nothing is scored, finishes when the count is in", () => {
  assert.equal(pollVerdict({ scored: 0, expected: 15, polls: 1 }), "wait");
  assert.equal(pollVerdict({ scored: 15, expected: 15, polls: 3 }), "done");
  // Capped expected, more rows than expected: still finished.
  assert.equal(pollVerdict({ scored: 20, expected: 15, polls: 3 }), "done");
});

test("nothing expected means nothing to wait for", () => {
  assert.equal(pollVerdict({ scored: 0, expected: 0, polls: 1 }), "done");
});

test("the client gives up on its own: a run that died leaves scored < expected forever", () => {
  assert.equal(pollVerdict({ scored: 0, expected: 15, polls: MAX_POLLS - 1 }), "wait");
  assert.equal(pollVerdict({ scored: 0, expected: 15, polls: MAX_POLLS }), "stop");
  // A late success on the last poll is still a success, not a timeout.
  assert.equal(pollVerdict({ scored: 15, expected: 15, polls: MAX_POLLS }), "done");
});

test("every refusal reason has its own sentence; an already-scored account has none", () => {
  assert.equal(firstRunNotice("already_scored"), null);
  assert.equal(firstRunNotice("no_resume"), "onboardNoResume");
  assert.equal(firstRunNotice("budget_paused"), "onboardPaused");
  assert.equal(firstRunNotice("no_preference"), "onboardGeneric");
  // A reason the server grows later must not read as success.
  assert.equal(firstRunNotice("something_new"), "onboardGeneric");
  assert.equal(firstRunNotice(null), "onboardGeneric");
});

test("chip edits are case-insensitive, so a stored TW and a picked tw are one value", () => {
  assert.deepEqual(addValue(["tw"], "TW"), ["tw"]);
  assert.deepEqual(addValue(["tw"], "jp"), ["tw", "jp"]);
  assert.deepEqual(removeValue(["TW", "jp"], "tw"), ["jp"]);
});

test("saving needs at least one answer", () => {
  assert.equal(canSave({ functions: [], locations: [], seniority: "" }), false);
  assert.equal(canSave({ functions: ["backend"], locations: [], seniority: "" }), true);
  assert.equal(canSave({ functions: [], locations: [], seniority: "senior" }), true);
});
