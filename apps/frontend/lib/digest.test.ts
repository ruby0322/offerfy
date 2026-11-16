import assert from "node:assert/strict";
import test from "node:test";
import {
  STRONG_TIER,
  TRACK_ORDER,
  groupByTrack,
  strongFitCount,
  uniqueByJob,
  type DigestItem,
} from "./digest";

function item(job_id: string, track: string, rank: number): DigestItem {
  return {
    job_id,
    job_title: job_id,
    company: "Acme",
    apply_url: "https://example.com/apply",
    job_is_active: true,
    track,
    rank,
    tier: null,
    fit_score: null,
    reason: null,
    gap: null,
  };
}

test("groupByTrack returns tracks in TRACK_ORDER and omits empty ones", () => {
  const groups = groupByTrack([item("a", "new_this_week", 1), item("b", "strong_fit", 1)]);
  assert.deepEqual(groups.map((g) => g.track), ["strong_fit", "new_this_week"]);
  assert.deepEqual(TRACK_ORDER, ["strong_fit", "new_this_week"]);
  assert.deepEqual(groupByTrack([item("a", "new_this_week", 1)]).map((g) => g.track), ["new_this_week"]);
  assert.deepEqual(groupByTrack([]), []);
});

test("a posting written into both tracks is rendered once, under the first", () => {
  // Digests built before the tracks were made disjoint hold exactly this, and
  // a digest is frozen once written.
  const groups = groupByTrack([item("a", "strong_fit", 1), item("a", "new_this_week", 1)]);
  assert.deepEqual(groups.map((g) => g.track), ["strong_fit"]);
  assert.deepEqual(groups[0].items.map((i) => i.job_id), ["a"]);
});

test("de-duplication keeps the counts per heading honest", () => {
  const groups = groupByTrack([
    item("a", "strong_fit", 1),
    item("b", "strong_fit", 2),
    item("a", "new_this_week", 1),
    item("c", "new_this_week", 2),
  ]);
  assert.deepEqual(groups.map((g) => [g.track, g.items.length]), [
    ["strong_fit", 2],
    ["new_this_week", 1],
  ]);
  // The headings sum to the number of distinct postings on the card.
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  assert.equal(total, new Set(groups.flatMap((g) => g.items.map((i) => i.job_id))).size);
  assert.equal(total, 3);
});

test("an unknown track does not vanish from the grouping", () => {
  const groups = groupByTrack([item("a", "unranked", 1), item("b", "strong_fit", 1), item("c", "future", 1)]);
  assert.deepEqual(groups.map((g) => g.track), ["strong_fit", "unranked", "future"]);
  assert.deepEqual(groupByTrack([item("a", "unranked", 1)]).map((g) => g.items.length), [1]);
});

test("groupByTrack keeps rank order inside a track", () => {
  const groups = groupByTrack([item("c", "strong_fit", 3), item("a", "strong_fit", 1), item("b", "strong_fit", 2)]);
  assert.deepEqual(groups[0].items.map((i) => i.job_id), ["a", "b", "c"]);
});

function rated(job_id: string, track: string, tier: string | null, fit_score: number | null): DigestItem {
  return { ...item(job_id, track, 1), tier, fit_score };
}

test("strongFitCount counts a posting in both tracks once", () => {
  // The symptom: "2 scored a strong fit" for one role.
  const items = [
    rated("a", "strong_fit", STRONG_TIER, 0.9),
    rated("a", "new_this_week", STRONG_TIER, 0.9),
  ];
  assert.equal(items.length, 2);
  assert.equal(strongFitCount(items), 1);
});

test("strongFitCount counts the rating, not a score threshold", () => {
  // tier is an argmax and fit_score a weighted sum of the same four
  // probabilities, so neither implies the other at 0.8.
  assert.equal(strongFitCount([rated("a", "strong_fit", STRONG_TIER, 0.78)]), 1);
  assert.equal(strongFitCount([rated("b", "strong_fit", "Good fit", 0.82)]), 0);
  assert.equal(strongFitCount([rated("c", "new_this_week", "Worth a look", 0.4)]), 0);
});

test("strongFitCount ignores a degraded digest's unrated rows", () => {
  assert.equal(strongFitCount([rated("a", "unranked", null, null)]), 0);
  assert.equal(strongFitCount([]), 0);
});

test("uniqueByJob keeps the first row for each posting, in order", () => {
  const items = [
    rated("b", "new_this_week", STRONG_TIER, 0.9),
    rated("b", "strong_fit", STRONG_TIER, 0.9),
    rated("a", "strong_fit", "Good fit", 0.7),
  ];
  assert.deepEqual(uniqueByJob(items).map((i) => [i.job_id, i.track]), [
    ["b", "new_this_week"],
    ["a", "strong_fit"],
  ]);
  assert.deepEqual(uniqueByJob([]), []);
});

test("uniqueByJob is what keeps a top-N slice from spending two slots on one role", () => {
  // The overview's picks column: sort by score, then take three.
  const items = [
    rated("a", "strong_fit", STRONG_TIER, 0.9),
    rated("a", "new_this_week", STRONG_TIER, 0.9),
    rated("b", "strong_fit", "Good fit", 0.7),
    rated("c", "new_this_week", "Worth a look", 0.5),
  ];
  const picks = uniqueByJob(
    [...items].sort((x, y) => (y.fit_score ?? 0) - (x.fit_score ?? 0)),
  ).slice(0, 3);
  assert.deepEqual(picks.map((i) => i.job_id), ["a", "b", "c"]);
  // And the keys a list would render by are distinct.
  assert.equal(new Set(picks.map((i) => i.job_id)).size, picks.length);
});
