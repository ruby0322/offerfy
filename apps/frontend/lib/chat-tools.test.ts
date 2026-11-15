import assert from "node:assert/strict";
import test from "node:test";

import {
  jobProfileMatchCount,
  jobSearchCount,
  parseToolPayload,
  readJobPosting,
} from "./chat-tools";

function payload(name: string, result: unknown) {
  return parseToolPayload(JSON.stringify({ name, arguments: {}, result }));
}

test("a search trace reports how many postings matched, not how many it showed", () => {
  // `total` is the filtered count; `returned` is the page. The card says the
  // total, which is the honest answer to "how many are there".
  const found = payload("search_jobs", { total: 37, returned: 5, items: [] });
  assert.equal(jobSearchCount(found), 37);
});

test("zero matches is zero, not absent", () => {
  // The card renders a different string for 0, so null and 0 must not collapse.
  assert.equal(jobSearchCount(payload("search_jobs", { total: 0, items: [] })), 0);
});

test("a search error carries no count", () => {
  assert.equal(jobSearchCount(payload("search_jobs", { error: "Invalid function" })), null);
});

test("a posting trace gives the title and the employer", () => {
  const read = payload("read_job", {
    job_id: "j1",
    title: "Payments Engineer",
    company: "Stripe",
    description: "...",
  });
  assert.deepEqual(readJobPosting(read), { title: "Payments Engineer", company: "Stripe" });
});

test("a posting that could not be read gives nothing to render", () => {
  assert.equal(readJobPosting(payload("read_job", { error: "No posting with job_id x" })), null);
});

test("the profile trace reports the live match count", () => {
  const profile = payload("read_my_job_profile", {
    preference: { target_functions: ["backend"] },
    match_count: 12,
    matches: [],
  });
  assert.equal(jobProfileMatchCount(profile), 12);
});

test("a guest's profile trace has no count to report", () => {
  // requires_login, not an error: the card shows "sign in required".
  const guest = payload("read_my_job_profile", { requires_login: true, note: "..." });
  assert.equal(jobProfileMatchCount(guest), null);
});

test("each reader ignores another tool's trace", () => {
  const edit = payload("apply_typst_edit", { total: 9, title: "x", company: "y", match_count: 3 });
  assert.equal(jobSearchCount(edit), null);
  assert.equal(readJobPosting(edit), null);
  assert.equal(jobProfileMatchCount(edit), null);
  assert.equal(jobSearchCount(null), null);
});
