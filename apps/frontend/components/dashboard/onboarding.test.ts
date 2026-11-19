import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_POLLS,
  addValue,
  canSave,
  cardVisible,
  firstRunNotice,
  needsFactsBuild,
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
  assert.equal(cardVisible({ updated_at: null }, { has_master: false, published: true }), false);
});

test("a new account's card builds the facts its own chips come from", () => {
  // The default new account: a published resume, and nothing has built the
  // ResumeFacts row the seed is computed from. Without this the card showed
  // no chips under "we read your resume and filled these in".
  assert.equal(needsFactsBuild({ facts_state: "missing" }), true);
  // Stale still yields a real seed, and the sweep repairs the row for free
  // on its next pass; paying to refine an answer about to be confirmed by
  // hand is not worth a credit.
  assert.equal(needsFactsBuild({ facts_state: "stale" }), false);
  assert.equal(needsFactsBuild({ facts_state: "fresh" }), false);
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
  // The one reason where work IS under way: it must not read as "did not
  // start, try the next regular pass".
  assert.equal(firstRunNotice("already_running"), "onboardRunning");
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
  assert.equal(canSave({ functions: [], locations: ["tw"], seniority: "" }), true);
});
