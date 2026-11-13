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

test("a posting appearing in both tracks appears in both groups", () => {
  const groups = groupByTrack([item("a", "strong_fit", 1), item("a", "new_this_week", 1)]);
  assert.equal(groups.length, 2);
  for (const g of groups) assert.deepEqual(g.items.map((i) => i.job_id), ["a"]);
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
