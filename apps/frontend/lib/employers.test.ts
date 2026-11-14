import assert from "node:assert/strict";
import test from "node:test";
import { employerHref, employerSlug } from "./employers";

// These pairs are the contract with the backend's own fold
// (services/jobs/employers.py, pinned by test_jobs_employers.py). If one side
// changes, every employer link on the board 404s.
test("the fold matches the backend's, case, spaces and punctuation alike", () => {
  assert.equal(employerSlug("Anthropic"), "anthropic");
  assert.equal(employerSlug("Scale AI"), "scale-ai");
  assert.equal(employerSlug("xAI"), "xai");
  assert.equal(employerSlug("Grafana Labs"), "grafana-labs");
  assert.equal(employerSlug("N26"), "n26");
  assert.equal(employerSlug("  Delivery Hero  "), "delivery-hero");
});

test("a name with no ascii alphanumerics yields no slug and no link", () => {
  assert.equal(employerSlug("台積電"), "");
  assert.equal(employerHref("台積電"), null);
});

test("href is the employer page's path", () => {
  assert.equal(employerHref("Together AI"), "/jobs/employers/together-ai");
});
