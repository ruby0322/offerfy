import assert from "node:assert/strict";
import test from "node:test";
import { hasSavedPreference, preferenceSummary, type SummaryLabels } from "./jobs";

const labels: SummaryLabels = {
  seniority: (v) => v,
  fn: (v) => v,
  country: (v) => (v === "TW" ? "Taiwan" : v),
  remote: "Remote",
  onsite: "On-site",
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

test("an on-site-only preference is a summary, not nothing", () => {
  assert.equal(preferenceSummary({ ...none, remote_required: false }, labels), "On-site");
});

test("hasSavedPreference needs a saved row that says something", () => {
  const saved = "2026-11-19T08:00:00+00:00";
  // The seed: a guess, never stored, however full it is.
  assert.equal(hasSavedPreference({ ...none, target_functions: ["backend"], updated_at: null }), false);
  // Saved but empty says nothing: asked about like a new account.
  assert.equal(hasSavedPreference({ ...none, updated_at: saved }), false);
  assert.equal(hasSavedPreference({ ...none, target_functions: ["backend"], updated_at: saved }), true);
  // Each axis counts on its own, matching what preferenceSummary prints.
  assert.equal(hasSavedPreference({ ...none, target_seniority: "senior", updated_at: saved }), true);
  assert.equal(hasSavedPreference({ ...none, remote_required: true, updated_at: saved }), true);
  assert.equal(hasSavedPreference({ ...none, remote_required: false, updated_at: saved }), true);
});
