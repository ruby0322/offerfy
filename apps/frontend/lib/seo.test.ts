import assert from "node:assert/strict";
import test from "node:test";
import {
  isJobIndexable,
  jobPostingJsonLd,
  organizationJsonLd,
  CONTACT_EMAIL,
  JOB_FRESHNESS_DAYS,
  SITE_NAME,
  SITE_URL,
} from "./seo";

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

test("Organization JSON-LD has name, description, url, and email contactPoint", () => {
  const org = organizationJsonLd();
  assert.equal(org["@type"], "Organization");
  assert.equal(org.name, SITE_NAME);
  assert.equal(org.url, SITE_URL);
  assert.equal(typeof org.description, "string");
  assert.ok(String(org.description).length > 20);
  assert.deepEqual(org.contactPoint, {
    "@type": "ContactPoint",
    contactType: "customer support",
    email: CONTACT_EMAIL,
  });
  assert.equal("address" in org, false);
});

const DAY_MS = 24 * 60 * 60 * 1000;

test("a posting seen just inside the freshness window is still indexable", () => {
  const now = new Date("2026-10-24T00:00:00.000Z");
  const seen = new Date(now.getTime() - (JOB_FRESHNESS_DAYS - 1) * DAY_MS);
  assert.equal(isJobIndexable(seen.toISOString(), now), true);
});

test("a posting past the freshness window is not indexable", () => {
  // The regression this guards: ingest stalled on 2026-09-01, every row stayed
  // is_active, and validThrough quietly expired on 2026-10-01 while 11k pages
  // kept advertising themselves to Google.
  const now = new Date("2026-10-24T00:00:00.000Z");
  assert.equal(isJobIndexable("2026-09-01T12:00:00.000Z", now), false);
});

test("validThrough is always ahead of now for an indexable posting", () => {
  const now = new Date("2026-10-24T00:00:00.000Z");
  const seen = new Date(now.getTime() - 1 * DAY_MS).toISOString();
  assert.equal(isJobIndexable(seen, now), true);
  const posting = jobPostingJsonLd({ ...base, lastSeenAt: seen, location: "Taipei", remote: false });
  assert.ok(posting);
  assert.ok(new Date(posting.validThrough as string).getTime() > now.getTime());
});

test("an unparseable last_seen_at is treated as not indexable", () => {
  assert.equal(isJobIndexable("not-a-date"), false);
});
