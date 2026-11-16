import assert from "node:assert/strict";
import test from "node:test";
import { TRACK_ORDER, groupByTrack, type DigestItem } from "./digest";

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
