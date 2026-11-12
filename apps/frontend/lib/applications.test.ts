import assert from "node:assert/strict";
import test from "node:test";
import { GROUP_ORDER, groupApplications, type Application } from "./applications";

function row(job_id: string, status: string, last_status_at: string): Application {
  return {
    job_id,
    job_title: job_id,
    company: "Acme",
    apply_url: "https://example.com/apply",
    job_is_active: true,
    status: status as Application["status"],
    source: "job_page",
    tailor_resume_id: null,
    applied_at: null,
    applied_confirmed_at: null,
    last_status_at,
    notes: "",
  };
}

test("groupApplications returns groups in GROUP_ORDER and omits empty ones", () => {
  const groups = groupApplications([
    row("a", "saved", "2026-11-01T00:00:00Z"),
    row("b", "interviewing", "2026-11-02T00:00:00Z"),
    row("c", "saved", "2026-11-03T00:00:00Z"),
  ]);
  assert.deepEqual(GROUP_ORDER, ["interviewing", "applied", "tailored", "saved"]);
  assert.deepEqual(groups.map((g) => g.status), ["interviewing", "saved"]);
});

test("groupApplications keeps recency order inside a group", () => {
  const groups = groupApplications([
    row("newest", "saved", "2026-11-03T00:00:00Z"),
    row("older", "saved", "2026-11-02T00:00:00Z"),
  ]);
  assert.deepEqual(groups[0].items.map((i) => i.job_id), ["newest", "older"]);
});

test("an unknown status does not vanish from the grouping", () => {
  const groups = groupApplications([
    row("a", "saved", "2026-11-01T00:00:00Z"),
    row("b", "closed", "2026-11-02T00:00:00Z"),
    row("c", "from_the_future", "2026-11-03T00:00:00Z"),
  ]);
  const last = groups.slice(1).map((g) => g.status);
  assert.equal(groups[0].status, "saved");
  assert.deepEqual(last, ["closed", "from_the_future"]);
  assert.equal(groups.flatMap((g) => g.items).length, 3);
});
