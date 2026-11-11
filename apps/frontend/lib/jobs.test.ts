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
