import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import type { Digest, DigestItem } from "@/lib/digest";
import SlotTwo from "./SlotTwo";

const router = { push() {}, replace() {}, back() {}, forward() {}, refresh() {}, prefetch() {} };

function render(node: ReactElement): string {
  return renderToStaticMarkup(
    createElement(
      AppRouterContext.Provider,
      { value: router as never },
      createElement(NextIntlClientProvider, { locale: "en", messages: en, children: node }),
    ),
  );
}

function item(over: Partial<DigestItem>): DigestItem {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://x.example/1",
    job_is_active: true,
    track: "strong_fit",
    rank: 1,
    tier: "Strong fit",
    fit_score: 90,
    reason: "Because",
    gap: null,
    ...over,
  };
}

function digest(items: DigestItem[], status: Digest["status"] = "full"): Digest {
  return { status, built_at: "2026-11-09T00:00:00Z", opened_at: null, candidate_count: items.length, items };
}

const slot = (d: Digest | null) =>
  render(createElement(SlotTwo, { digest: d, matchCount: 7, hasPreference: true }));

test("slot 2 shows the digest when it has items", () => {
  const html = slot(digest([item({})]));
  assert.ok(html.includes('data-testid="dashboard-digest"'), html);
  assert.ok(!html.includes('data-testid="dashboard-matches"'), html);
});

test("slot 2 falls back to the match count when the digest is null", () => {
  const html = slot(null);
  assert.ok(html.includes('data-testid="dashboard-matches"'), html);
  assert.ok(!html.includes('data-testid="dashboard-digest"'), html);
});

test("slot 2 falls back to the match count when both tracks are empty", () => {
  const html = slot(digest([], "empty"));
  assert.ok(html.includes('data-testid="dashboard-matches"'), html);
  assert.ok(!html.includes('data-testid="dashboard-digest"'), html);
});

// status and items can disagree (status records what the scoring run did, the
// card's rule is what there is to show); only the items decide.
test("a non-empty status with no items still falls back to the match count", () => {
  for (const status of ["full", "degraded"] as const) {
    const html = slot(digest([], status));
    assert.ok(html.includes('data-testid="dashboard-matches"'), html);
    assert.ok(!html.includes('data-testid="dashboard-digest"'), html);
  }
});

// Review Focus 5: the common case for a thin market, not an edge case
test("a degraded digest with items still shows the digest, not the fallback", () => {
  const html = slot(
    digest([item({ track: "unranked", tier: null, fit_score: null, reason: null })], "degraded"),
  );
  assert.ok(html.includes('data-testid="dashboard-digest"'), html);
  assert.ok(html.includes('data-testid="dashboard-digest-degraded"'), html);
  assert.ok(!html.includes('data-testid="dashboard-matches"'), html);
});
