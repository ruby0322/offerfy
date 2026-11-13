import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { COUNTRY_KEYS, FUNCTION_KEYS, REGION_KEYS, SENIORITY_KEYS } from "./facet-labels";

/**
 * These maps are the seam between a vocabulary that lives in the backend's
 * job_taxonomy.json and the strings that live in messages/*.json. Nothing
 * fails loudly when they drift: `label()` in FilterBar falls back to the raw
 * value, so a missing entry ships a filter that says "MY" where Malaysia
 * belongs — which is exactly what reached production for MX, MY, PH, TH and
 * VN, with countryMx written and orphaned in all three locales.
 *
 * Like companies.test.ts, this reads the backend's data files, which the
 * frontend image does not copy, so it runs on a full checkout only:
 *   ./node_modules/.bin/tsx --test components/jobs/facet-labels.test.ts
 */
const BACKEND = join(import.meta.dirname, "..", "..", "..", "backend");
const LOCALES = ["en", "zh-TW", "zh-CN"];

type Taxonomy = {
  location_countries: Record<string, string>;
  location_regions: Record<string, string>;
};

function taxonomy(): Taxonomy {
  return JSON.parse(readFileSync(join(BACKEND, "data", "job_taxonomy.json"), "utf8"));
}

function messages(locale: string): Record<string, string> {
  const raw = JSON.parse(readFileSync(join(import.meta.dirname, "..", "..", "messages", `${locale}.json`), "utf8"));
  return raw.jobs as Record<string, string>;
}

test("every country the taxonomy can derive has a label", () => {
  const derivable = new Set(Object.values(taxonomy().location_countries));
  const missing = [...derivable].filter((code) => !(code in COUNTRY_KEYS)).sort();
  assert.deepEqual(
    missing,
    [],
    `facet-labels.ts has no entry for: ${missing.join(", ")} — the filter would show the bare code`,
  );
});

test("every region the taxonomy can derive has a label", () => {
  const derivable = new Set(Object.values(taxonomy().location_regions));
  const missing = [...derivable].filter((region) => !(region in REGION_KEYS)).sort();
  assert.deepEqual(missing, [], `no region label for: ${missing.join(", ")}`);
});

test("every message key these maps point at exists, in all three locales", () => {
  // The other half of the same failure: an entry can name a key that was
  // never written, and next-intl throws at render rather than falling back.
  const referenced = [
    ...Object.values(COUNTRY_KEYS),
    ...Object.values(REGION_KEYS),
    ...Object.values(SENIORITY_KEYS),
    ...Object.values(FUNCTION_KEYS),
  ];
  for (const locale of LOCALES) {
    const strings = messages(locale);
    const missing = referenced.filter((key) => !(key in strings)).sort();
    assert.deepEqual(missing, [], `${locale} is missing: ${missing.join(", ")}`);
  }
});

test("no label points at an empty string", () => {
  for (const locale of LOCALES) {
    const strings = messages(locale);
    const blank = Object.values(COUNTRY_KEYS).filter((key) => !strings[key]?.trim());
    assert.deepEqual(blank, [], `${locale} has blank country labels: ${blank.join(", ")}`);
  }
});
