import assert from "node:assert/strict";
import test from "node:test";
import type { MatchArrival } from "@/lib/dashboard";
import { arrivalHeadline, arrivalState, groupArrivals, strongArrivalCount } from "./arrivals";

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

test("an unscored arrival renders without a score, not as a poor match", () => {
  const groups = groupArrivals([
    arrival({
      job_id: "u",
      tier: null,
      fit_score: null,
      reason: null,
      gap: null,
      scored_at: null,
    }),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].day, null);
  assert.equal(strongArrivalCount(groups.flatMap((g) => g.items)), 0);
});

test("an inactive posting is marked rather than hidden", () => {
  const groups = groupArrivals([arrival({ job_id: "closed", job_is_active: false })]);
  assert.equal(groups[0].items.length, 1);
  assert.equal(groups[0].items[0].job_is_active, false);
});

test("arrivals group by the day they were scored, newest first, unscored last", () => {
  const groups = groupArrivals([
    arrival({ job_id: "old", scored_at: "2026-11-17T03:00:00Z" }),
    arrival({ job_id: "unscored", tier: null, fit_score: null, scored_at: null }),
    arrival({ job_id: "new1", scored_at: "2026-11-18T03:00:00Z" }),
    arrival({ job_id: "new2", scored_at: "2026-11-18T05:00:00Z" }),
  ]);
  assert.deepEqual(
    groups.map((g) => [g.day, g.items.map((i) => i.job_id)]),
    [
      ["2026-11-18", ["new1", "new2"]],
      ["2026-11-17", ["old"]],
      [null, ["unscored"]],
    ],
  );
});

test("the day is a Taipei calendar day, not a UTC one", () => {
  // 17:00Z is already the next morning in Taipei.
  const groups = groupArrivals([arrival({ scored_at: "2026-11-17T17:00:00Z" })]);
  assert.equal(groups[0].day, "2026-11-18");
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
