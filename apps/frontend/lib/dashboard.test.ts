import assert from "node:assert/strict";
import test from "node:test";
import { fetchDashboard } from "./dashboard";

/**
 * The dashboard fetcher's defaults, which exist because this host rolls three
 * web instances and one API separately: web has been ahead of the API before,
 * and the overview reads `dash.arrivals.length` while rendering.
 */

function stubFetch(body: unknown) {
  const original = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), { status: 200 })) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

const OLD_API = {
  preference: {
    target_functions: ["backend"],
    target_locations: ["tw"],
    target_seniority: "senior",
    remote_required: null,
    activity: "actively_looking",
    updated_at: "2026-11-12T00:00:00Z",
    facts_state: "fresh",
  },
  match_count: 3,
  matches: [],
  applications: [],
  resume_health: {
    has_master: true,
    published: true,
    facts_state: "fresh",
    matching_revision_current: true,
    needs_rebuild: false,
  },
  employer_views: { disclosure_count: 0, last_disclosed_at: null },
  digest: null,
};

test("an API with no arrivals field degrades instead of blanking the page", async () => {
  const restore = stubFetch(OLD_API);
  try {
    const dash = await fetchDashboard();
    // The overview's own render does this. Undefined here threw.
    assert.equal(dash.arrivals.length, 0);
    // False, not true: an absent answer is not evidence of a first visit, and
    // "Welcome back." is the wrong thing to say to someone it cannot place.
    assert.equal(dash.first_visit, false);
    // Nothing else is invented.
    assert.equal(dash.match_count, 3);
  } finally {
    restore();
  }
});

test("a real answer is passed through untouched", async () => {
  const restore = stubFetch({
    ...OLD_API,
    arrivals: [{ job_id: "j1" }],
    first_visit: true,
  });
  try {
    const dash = await fetchDashboard();
    assert.equal(dash.arrivals.length, 1);
    assert.equal(dash.first_visit, true);
  } finally {
    restore();
  }
});
