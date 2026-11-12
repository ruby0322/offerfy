import assert from "node:assert/strict";
import test from "node:test";
import { hasJobFilters, jobListParams, jobQueryString } from "./jobs";

test("jobListParams carries every facet", () => {
  const params = jobListParams({ q: "backend", country: "TW", region: "apac", seniority: "senior",
    function: "backend", postedWithinDays: "7" });
  assert.equal(params.get("country"), "TW");
  assert.equal(params.get("region"), "apac");
  assert.equal(params.get("seniority"), "senior");
  assert.equal(params.get("function"), "backend");
  assert.equal(params.get("posted_within_days"), "7");
});

test("jobListParams omits blank facets", () => {
  const params = jobListParams({ country: "", region: undefined, seniority: undefined });
  assert.equal(params.has("country"), false);
  assert.equal(params.has("region"), false);
  assert.equal(params.has("seniority"), false);
});

test("hasJobFilters sees a facet-only query", () => {
  assert.equal(hasJobFilters({ seniority: "senior" }), true);
  assert.equal(hasJobFilters({}), false);
});

test("jobQueryString drops limit but keeps facets", () => {
  assert.equal(jobQueryString({ country: "TW" }), "?country=TW");
});

function stubFetch(body: unknown, calls: { url: string; init?: RequestInit }[]) {
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status: 200 });
  }) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

test("saveJobPreferences sends lists, not pipe-delimited strings", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const restore = stubFetch(
    {
      target_functions: ["backend", "data"],
      target_locations: ["tw"],
      target_seniority: "senior",
      remote_required: false,
      activity: "browsing",
      updated_at: "2026-11-12T00:00:00Z",
      facts_state: "fresh",
    },
    calls,
  );
  try {
    const { saveJobPreferences } = await import("./jobs");
    await saveJobPreferences({
      target_functions: ["backend", "data"],
      target_locations: ["tw"],
      target_seniority: "senior",
      remote_required: false,
      activity: "browsing",
    });
  } finally {
    restore();
  }
  assert.equal(calls[0].url, "/api/v1/me/job-preferences");
  assert.equal(calls[0].init?.method, "PUT");
  const sent = JSON.parse(String(calls[0].init?.body));
  assert.deepEqual(sent.target_functions, ["backend", "data"]);
  assert.deepEqual(sent.target_locations, ["tw"]);
  // StrictBool server-side: a string or 0/1 here is a 422.
  assert.strictEqual(sent.remote_required, false);
});

test("fetchJobPreferences maps an absent seniority to null, not the string 'null'", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const restore = stubFetch(
    {
      target_functions: [],
      target_locations: [],
      activity: "actively_looking",
      updated_at: "2026-11-12T00:00:00Z",
      facts_state: "missing",
    },
    calls,
  );
  try {
    const { fetchJobPreferences } = await import("./jobs");
    const pref = await fetchJobPreferences();
    assert.strictEqual(pref.target_seniority, null);
    assert.strictEqual(pref.remote_required, null);
    assert.equal(pref.facts_state, "missing");
  } finally {
    restore();
  }
});

test("refreshResumeFacts surfaces a 200-with-error rather than throwing", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const restore = stubFetch({ facts_state: "missing", error: "no_source" }, calls);
  try {
    const { refreshResumeFacts } = await import("./jobs");
    const out = await refreshResumeFacts();
    assert.equal(out.error, "no_source");
    assert.equal(calls[0].init?.method, "POST");
  } finally {
    restore();
  }
});

test("saveJobPreferences keeps null as null for remote and seniority", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const restore = stubFetch({}, calls);
  try {
    const { saveJobPreferences } = await import("./jobs");
    await saveJobPreferences({
      target_functions: [],
      target_locations: [],
      target_seniority: null,
      remote_required: null,
      activity: "paused",
    });
  } finally {
    restore();
  }
  const sent = JSON.parse(String(calls[0].init?.body));
  assert.strictEqual(sent.remote_required, null);
  assert.strictEqual(sent.target_seniority, null);
  assert.ok("remote_required" in sent && "target_seniority" in sent);
});

test("remote tri-state maps to select values and back without loss", async () => {
  const { remoteToValue, valueToRemote } = await import("./jobs");
  for (const v of [true, false, null] as const) {
    assert.strictEqual(valueToRemote(remoteToValue(v)), v);
  }
  assert.equal(remoteToValue(false), "false");
  assert.strictEqual(valueToRemote(""), null);
});
