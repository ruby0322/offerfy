import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_POLLS,
  READ_TIMEOUT_MS,
  addValue,
  canSave,
  cardVisible,
  firstRunNotice,
  needsFactsBuild,
  pollVerdict,
  removeValue,
  withDeadline,
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
  // Their own setting, which they can undo, is not the platform's weekly
  // limit, which they cannot -- and the sweep skips them, so the generic
  // "scored in the next regular pass" would be a promise nothing keeps.
  assert.equal(firstRunNotice("activity_paused"), "onboardActivityPaused");
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

// The reading phase's bound. Everything the card does while scoring is already
// bounded by `pollVerdict`; before this the read was not, so a request that
// never settled -- a sleeping laptop, a dropped connection, an extraction that
// hangs past the server's own ceiling -- left the spinner up and the three
// questions unreachable for as long as the tab stayed open.

test("a read that answers in time answers", async () => {
  assert.equal(await withDeadline(Promise.resolve("seed"), 1000), "seed");
});

// The explicit timeout is what makes this a failure rather than a hang:
// without a deadline the promise below never settles, and a suite that hangs
// tells an operator far less than one that fails.
test("a read that never settles stops being waited for", { timeout: 2000 }, async () => {
  // The defect this closes: the card showed a spinner instead of the
  // questions, and nothing moved it off.
  assert.equal(await withDeadline(new Promise(() => {}), 5), null);
});

test("a read that fails is a failure, not a timeout", async () => {
  // The card tells those apart in the opposite direction to the clock: a
  // refusal is known at once and must not be waited out.
  await assert.rejects(
    withDeadline(Promise.reject(new Error("429")), 1000),
    /429/,
  );
});

test("an answer that arrives after the deadline is discarded", async () => {
  // Not merely late: by then the person is answering the questions by hand,
  // and seeding the chips underneath them would overwrite what they typed.
  // Resolving null first is what makes that structurally impossible -- there
  // is nowhere for the late value to go.
  const late = new Promise((r) => setTimeout(() => r("seed"), 40));
  assert.equal(await withDeadline(late, 5), null);
  // And the late rejection of an abandoned read is not an unhandled one.
  const lateFail = new Promise((_, r) => setTimeout(() => r(new Error("boom")), 40));
  assert.equal(await withDeadline(lateFail, 5), null);
  await new Promise((r) => setTimeout(r, 60));
});

test("the bound outlasts the extraction ceiling, and not much more", async () => {
  // resume_facts.PROFILE_TIMEOUT is 90 s, so a shorter bound here would throw
  // away seeds the server was still going to send -- and bill for.
  assert.ok(READ_TIMEOUT_MS > 90_000, String(READ_TIMEOUT_MS));
  // And a ceiling, because a bound nobody reaches is the defect again under
  // another name: past a couple of minutes the card is hung as far as the
  // person sitting in front of it is concerned.
  assert.ok(READ_TIMEOUT_MS <= 180_000, String(READ_TIMEOUT_MS));
});
