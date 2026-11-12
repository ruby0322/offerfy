import assert from "node:assert/strict";
import test from "node:test";
import { preferenceSummary, type SummaryLabels } from "./jobs";

const labels: SummaryLabels = {
  seniority: (v) => v,
  fn: (v) => v,
  country: (v) => (v === "TW" ? "Taiwan" : v),
  remote: "Remote",
};
const none = { target_functions: [], target_locations: [], target_seniority: null, remote_required: null };

test("the summary has the spec's shape", () => {
  assert.equal(
    preferenceSummary(
      { ...none, target_seniority: "senior", target_functions: ["backend", "infra"], target_locations: ["tw"] },
      labels,
    ),
    "senior · backend, infra · Taiwan",
  );
});

test("a preference that says nothing summarises to an empty string", () => {
  assert.equal(preferenceSummary(none, labels), "");
});

test("remote-only replaces the location list", () => {
  assert.equal(
    preferenceSummary({ ...none, target_functions: ["data"], target_locations: ["tw"], remote_required: true }, labels),
    "data · Remote",
  );
});
