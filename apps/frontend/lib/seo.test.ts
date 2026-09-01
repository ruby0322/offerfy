import assert from "node:assert/strict";
import test from "node:test";
import { jobPostingJsonLd } from "./seo";

const base = {
  locale: "en" as const,
  href: "/jobs/job-1",
  jobId: "job-1",
  title: "Staff Engineer",
  descriptionHtml: "<p>Build the editor.</p>",
  descriptionText: "Build the editor.",
  company: "Acme",
  datePosted: "2026-08-01T00:00:00.000Z",
  lastSeenAt: "2026-09-01T12:00:00.000Z",
};

test("remote plus location emits TELECOMMUTE, Place, identifier, and validThrough", () => {
  const posting = jobPostingJsonLd({
    ...base,
    location: "Remote - United States",
    remote: true,
  });
  assert.ok(posting);
  assert.equal(posting["@type"], "JobPosting");
  assert.equal(posting.url, "https://offerfy.cc/en/jobs/job-1");
  assert.equal(posting.directApply, false);
  assert.equal(posting.applyUrl, undefined);
  assert.equal(posting.description, "<p>Build the editor.</p>");
  assert.equal(posting.datePosted, "2026-08-01T00:00:00.000Z");
  assert.equal(posting.validThrough, "2026-10-01T12:00:00.000Z");
  assert.equal(posting.jobLocationType, "TELECOMMUTE");
  assert.deepEqual(posting.identifier, {
    "@type": "PropertyValue",
    name: "Offerfy",
    value: "job-1",
  });
  assert.deepEqual(posting.hiringOrganization, {
    "@type": "Organization",
    name: "Acme",
  });
  assert.deepEqual(posting.jobLocation, {
    "@type": "Place",
    address: {
      "@type": "PostalAddress",
      addressLocality: "Remote - United States",
    },
  });
});

test("omits JobPosting when there is no remote flag and no location", () => {
  const posting = jobPostingJsonLd({
    ...base,
    location: null,
    remote: null,
  });
  assert.equal(posting, null);
});

test("wraps plain description text in a paragraph when HTML is empty", () => {
  const posting = jobPostingJsonLd({
    ...base,
    descriptionHtml: "",
    descriptionText: "Build things",
    location: "Taipei",
    remote: false,
  });
  assert.ok(posting);
  assert.equal(posting.description, "<p>Build things</p>");
  assert.equal(posting.jobLocationType, undefined);
});
