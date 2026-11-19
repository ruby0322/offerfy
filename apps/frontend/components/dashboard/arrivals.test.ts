import assert from "node:assert/strict";
import test from "node:test";
import type { MatchArrival } from "@/lib/dashboard";
import { arrivalHeadline, arrivalState, strongArrivalCount } from "./arrivals";

function arrival(over: Partial<MatchArrival> = {}): MatchArrival {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    location: "Taipei",
    tier: "Good fit",
    fit_score: 0.7,
    reason: "Relevant stack.",
    gap: null,
    scored_at: "2026-11-18T03:00:00Z",
    ...over,
  };
}

test("a first visit is not greeted as a return", () => {
  // The "Welcome back." bug: a NULL marker used to share a branch with
  // cleared browser storage, and a first-run person with fifteen fresh
  // matches was greeted as a returning one.
  for (const count of [0, 15]) {
    const h = arrivalHeadline(count, true);
    assert.equal(h.kind, "first_visit");
    assert.equal(h.count, count);
  }
  assert.notEqual(arrivalHeadline(3, true).kind, arrivalHeadline(3, false).kind);
});

test("no arrivals and not a first visit says nothing new", () => {
  assert.deepEqual(arrivalHeadline(0, false), { kind: "nothing_new", count: 0 });
});

test("the headline counts arrivals, not the removed new_match_count", () => {
  assert.deepEqual(arrivalHeadline(4, false), { kind: "arrivals", count: 4 });
});

test("an arrival with no score counts as no strong fit", () => {
  // Not a poor match -- nobody has judged it, so it is simply not counted.
  const unscored = arrival({ tier: null, fit_score: null, reason: null, scored_at: null });
  assert.equal(strongArrivalCount([unscored]), 0);
});




test("strong arrivals are counted by tier, once per posting", () => {
  const items = [
    arrival({ job_id: "a", tier: "Strong fit" }),
    arrival({ job_id: "a", tier: "Strong fit" }),
    arrival({ job_id: "b", tier: "Good fit", fit_score: 0.95 }),
  ];
  assert.equal(strongArrivalCount(items), 1);
});

test("three states: scored, still scoring, and judged-without-a-score", () => {
  const scored = arrival();
  const pending = arrival({ tier: null, fit_score: null, reason: null, scored_at: null });
  // A score row exists (scored_at set) but carries no score: a refusal. It is
  // not "still scoring" -- that promises a score that will never arrive.
  const refused = arrival({ tier: null, fit_score: null, reason: null });
  assert.equal(arrivalState(scored), "scored");
  assert.equal(arrivalState(pending), "pending");
  assert.equal(arrivalState(refused), "unscored");
});

test("a score of exactly zero is scored, not pending", () => {
  // 0 is falsy; a truthiness check would call a real zero "still scoring".
  assert.equal(arrivalState(arrival({ fit_score: 0, tier: "Worth a look" })), "scored");
});
