import assert from "node:assert/strict";
import test from "node:test";
import type { Application } from "@/lib/applications";
import { applicationMetrics, median, QUIET_DAYS } from "./applicationMetrics";

const NOW = new Date("2026-11-15T12:00:00Z");

function row(over: Partial<Application> = {}): Application {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    status: "saved",
    source: "search",
    tailor_resume_id: null,
    applied_at: null,
    applied_confirmed_at: null,
    replied_at: null,
    last_status_at: "2026-11-10T12:00:00Z",
    notes: null,
    ...over,
  };
}

/** Days before NOW, as an ISO string. */
function ago(days: number): string {
  return new Date(NOW.getTime() - days * 86_400_000).toISOString();
}

test("an empty pipeline reports no reply rate rather than zero per cent", () => {
  const m = applicationMetrics([], NOW);
  assert.equal(m.sent, 0);
  assert.equal(m.replyRate, null, "0% from nothing sent reads as a verdict");
  assert.equal(m.medianDaysToReply, null);
  assert.equal(m.neverSentOldestDays, null);
});

test("sent counts every row ever sent, not rows sitting at applied", () => {
  // The denominator has to be stable as rows move on, or the reply rate
  // climbs every time someone logs a response.
  const rows = [
    row({ status: "applied", applied_at: ago(3) }),
    row({ status: "interviewing", applied_at: ago(20), replied_at: ago(9) }),
    row({ status: "closed", applied_at: ago(40), replied_at: ago(30) }),
    row({ status: "closed", applied_at: ago(50) }),
    row({ status: "tailored" }),
    row({ status: "saved" }),
  ];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.sent, 4);
  assert.equal(m.replied, 2);
  assert.equal(m.replyRate, 50);
});

test("a reply is the stamp, never the status", () => {
  // Both rows are closed. Only the stamp says which one was answered, which
  // is the entire reason the column exists.
  const rows = [
    row({ status: "closed", applied_at: ago(30), replied_at: ago(20) }),
    row({ status: "closed", applied_at: ago(30) }),
  ];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.replied, 1);
  assert.equal(m.replyRate, 50);
});

test("waiting counts applied rows, and flags the ones past the quiet line", () => {
  const rows = [
    row({ status: "applied", applied_at: ago(30), last_status_at: ago(30) }),
    row({ status: "applied", applied_at: ago(2), last_status_at: ago(2) }),
    // Exactly on the line is not yet past it.
    row({
      status: "applied",
      applied_at: ago(QUIET_DAYS),
      last_status_at: ago(QUIET_DAYS),
    }),
  ];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.waiting, 3);
  assert.equal(m.waitingTooLong, 1);
});

test("never sent is the tailored rows, aged by the oldest", () => {
  const rows = [
    row({ status: "tailored", last_status_at: ago(6) }),
    row({ status: "tailored", last_status_at: ago(2) }),
    row({ status: "applied", applied_at: ago(1) }),
  ];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.neverSent, 2);
  assert.equal(m.neverSentOldestDays, 6);
});

test("median days to reply uses the middle case, not the mean", () => {
  // One answer after four months must not drag the figure somewhere no
  // individual application has ever been.
  const rows = [
    row({ status: "closed", applied_at: ago(130), replied_at: ago(10) }),
    row({ status: "interviewing", applied_at: ago(14), replied_at: ago(11) }),
    row({ status: "closed", applied_at: ago(20), replied_at: ago(15) }),
  ];
  const m = applicationMetrics(rows, NOW);
  // Lags are 120, 3 and 5 days; the median is 5 where the mean would be ~43.
  assert.equal(m.medianDaysToReply, 5);
});

test("a reply with no send date counts as a reply but cannot be timed", () => {
  // A row stamped before applied_at existed, or stamped without a send.
  const rows = [row({ status: "closed", replied_at: ago(5) })];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.replied, 1);
  assert.equal(m.sent, 0);
  assert.equal(m.replyRate, null);
  assert.equal(m.medianDaysToReply, null, "no duration to take a median of");
});

test("a reply stamped before the send is ignored for timing", () => {
  const rows = [row({ status: "closed", applied_at: ago(5), replied_at: ago(9) })];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.replied, 1);
  assert.equal(m.medianDaysToReply, null);
});

test("unparseable timestamps do not produce NaN", () => {
  const rows = [
    row({ status: "applied", applied_at: "not-a-date", last_status_at: "nope" }),
    row({ status: "tailored", last_status_at: "also-not" }),
  ];
  const m = applicationMetrics(rows, NOW);
  assert.equal(m.waiting, 1);
  assert.equal(m.waitingTooLong, 0);
  assert.equal(m.neverSent, 0, "an unreadable date contributes no age");
  assert.equal(m.neverSentOldestDays, null);
});

test("median averages the middle two on an even count", () => {
  assert.equal(median([]), null);
  assert.equal(median([7]), 7);
  assert.equal(median([1, 2, 3, 4]), 3, "mean of 2 and 3, rounded");
  assert.equal(median([4, 1, 3, 2]), 3, "order of the input does not matter");
});
