import assert from "node:assert/strict";
import test from "node:test";
import {
  formatAgeHours,
  formatDuration,
  ingestVerdict,
  isBadVerdict,
  latestFailingBoards,
} from "./admin-ingest";
import type { AdminIngestHealth, AdminIngestPass } from "./api";

const health = (over: Partial<AdminIngestHealth> = {}): AdminIngestHealth => ({
  freshest_last_seen_at: "2026-10-24T07:00:00.000Z",
  catalog_age_hours: 2,
  fresh_job_count: 10831,
  stale_job_count: 0,
  freshness_days: 30,
  expected_interval_hours: 6,
  overdue: false,
  last_failure_at: null,
  ...over,
});

const pass = (over: Partial<AdminIngestPass> = {}): AdminIngestPass => ({
  run_id: "run-1",
  started_at: "2026-10-24T07:00:00.000Z",
  finished_at: "2026-10-24T07:02:00.000Z",
  duration_seconds: 120,
  sources_ok: 55,
  sources_failed: 0,
  upserted: 10831,
  expired: 0,
  status: "ok",
  failing_boards: [],
  ...over,
});

test("a refreshed catalog with nothing expiring reads as fresh", () => {
  assert.equal(ingestVerdict(health()), "fresh");
  assert.equal(isBadVerdict("fresh"), false);
});

test("an empty catalog is not healthy just because nothing is old", () => {
  const verdict = ingestVerdict(health({ freshest_last_seen_at: null, catalog_age_hours: null }));
  assert.equal(verdict, "empty");
  assert.equal(isBadVerdict(verdict), true);
});

test("overdue outranks expiring", () => {
  assert.equal(ingestVerdict(health({ overdue: true, stale_job_count: 50 })), "overdue");
});

test("a catalog refreshed on schedule can still be shedding pages", () => {
  // The 2026-10-01 outage shape: on-cadence, but pages already noindexed.
  const verdict = ingestVerdict(health({ stale_job_count: 42 }));
  assert.equal(verdict, "expiring");
  assert.equal(isBadVerdict(verdict), false);
});

test("age formatting switches precision and tolerates null", () => {
  assert.equal(formatAgeHours(null), "—");
  assert.equal(formatAgeHours(2.04), "2.0h");
  assert.equal(formatAgeHours(53.6), "54h");
});

test("duration formatting tolerates an unfinished pass", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(30), "30s");
  assert.equal(formatDuration(102), "2m");
});

test("failing boards come from the most recent pass that had any", () => {
  assert.equal(latestFailingBoards([]), null);
  assert.equal(latestFailingBoards([pass(), pass({ run_id: "run-0" })]), null);
  const withFailures = pass({
    run_id: "run-2",
    status: "degraded",
    sources_failed: 63,
    failing_boards: [{ source: "greenhouse", board_slug: "tsmc", error_snippet: "board not found" }],
  });
  assert.equal(latestFailingBoards([pass(), withFailures])?.run_id, "run-2");
});
