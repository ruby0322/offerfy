import assert from "node:assert/strict";
import test from "node:test";
import type { Application } from "@/lib/applications";
import { nextAction, quietDays, readyAgeDays, responseChoices } from "./applications";

const NOW = new Date("2026-11-13T12:00:00Z");

function row(over: Partial<Application> = {}): Application {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    status: "saved",
    source: "manual",
    tailor_resume_id: null,
    applied_at: null,
    applied_confirmed_at: null,
    replied_at: null,
    last_status_at: "2026-11-10T12:00:00Z",
    notes: null,
    ...over,
  };
}

test("nextAction maps each status to exactly one action", () => {
  assert.equal(nextAction(row({ status: "saved" })), "tailor");
  assert.equal(nextAction(row({ status: "tailored" })), "apply");
  assert.equal(
    nextAction(row({ status: "applied", applied_confirmed_at: "2026-11-11T00:00:00Z" })),
    "logResponse",
  );
  assert.equal(nextAction(row({ status: "interviewing" })), "logResponse");
  assert.equal(nextAction(row({ status: "closed" })), null);
});

test("an applied row never confirmed asks first", () => {
  assert.equal(nextAction(row({ status: "applied", applied_confirmed_at: null })), "confirm");
  // Only `applied` is ever asked; the others carry no such question.
  assert.equal(nextAction(row({ status: "interviewing", applied_confirmed_at: null })), "logResponse");
});

test("quietDays counts from last_status_at, not created_at", () => {
  const r = row({ status: "applied", last_status_at: "2026-11-06T12:00:00Z", applied_at: "2026-10-01T00:00:00Z" });
  assert.equal(quietDays(r, NOW), 7);
  assert.equal(quietDays(row({ status: "applied", last_status_at: "2026-11-13T01:00:00Z" }), NOW), 0);
  // A clock a little behind the server must not produce a negative count.
  assert.equal(quietDays(row({ status: "applied", last_status_at: "2026-11-14T00:00:00Z" }), NOW), 0);
});

test("readyAgeDays is reported for tailored rows only", () => {
  assert.equal(readyAgeDays(row({ status: "tailored", last_status_at: "2026-11-10T12:00:00Z" }), NOW), 3);
  for (const status of ["saved", "applied", "interviewing", "closed"] as const) {
    assert.equal(readyAgeDays(row({ status }), NOW), null);
  }
});

test("log a response is an explicit choice, and the only writer of interviewing and closed", () => {
  const statuses = (app: Application) => responseChoices(app).map((c) => c.status);
  assert.deepEqual(statuses(row({ status: "applied" })), [
    "interviewing",
    "closed",
    "closed",
  ]);
  assert.deepEqual(statuses(row({ status: "interviewing" })), ["closed"]);
  for (const status of ["saved", "tailored", "closed"] as const) {
    assert.deepEqual(statuses(row({ status })), []);
  }
});

test("closing an applied row asks whether anyone replied; interviewing does not", () => {
  // The two ways to close an applied row differ only in `replied`, which is
  // the fact the status cannot carry once the row is closed.
  const applied = responseChoices(row({ status: "applied" }));
  assert.deepEqual(
    applied.map((c) => [c.key, c.status, c.replied]),
    [
      ["responseHeardBack", "interviewing", undefined],
      ["responseSaidNo", "closed", true],
      ["responseNoAnswer", "closed", false],
    ],
  );
  // An interviewing row is already recorded as having heard back, so closing
  // it says nothing about a reply rather than risking overwriting one.
  const interviewing = responseChoices(row({ status: "interviewing" }));
  assert.deepEqual(
    interviewing.map((c) => [c.key, c.replied]),
    [["responseNotMoving", undefined]],
  );
});
