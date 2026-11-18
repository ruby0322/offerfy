import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import ArrivalsBlock from "./ArrivalsBlock";
import type { MatchArrival } from "@/lib/dashboard";

const router = {
  push() {},
  replace() {},
  back() {},
  forward() {},
  refresh() {},
  prefetch() {},
};

function render(node: ReactElement): string {
  return renderToStaticMarkup(
    createElement(
      AppRouterContext.Provider,
      { value: router as never },
      createElement(
        NextIntlClientProvider,
        { locale: "en", messages: en, timeZone: "Asia/Taipei" },
        node,
      ),
    ),
  );
}

function arrival(n: number): MatchArrival {
  return {
    job_id: `job-${n}`,
    job_title: `Role ${n}`,
    company: "Acme",
    apply_url: "https://example.com/apply",
    job_is_active: true,
    location: "Taipei",
    tier: "Strong fit",
    fit_score: 0.8,
    reason: "Because reasons.",
    gap: null,
    scored_at: "2026-11-19T03:00:00Z",
  };
}

test("the overflow affordance opens the rest here, not on a page that has none", () => {
  // The Matches tab renders the weekly picks and the ingest, never arrivals,
  // and by the time anyone clicks, the window this page showed has been
  // marked seen -- so a link there was "see the rest" pointing at nothing.
  const html = render(createElement(ArrivalsBlock, { arrivals: [...Array(12)].map((_, i) => arrival(i)) }));
  assert.match(html, /<button[^>]*>Show 2 more<\/button>/);
  assert.ok(
    !html.includes('href="/en/dashboard/matches"') && !html.includes('href="/dashboard/matches"'),
    "the overflow must not navigate to a page that shows no arrivals",
  );
});

test("ten rows, and the rest wait behind the control", () => {
  const html = render(createElement(ArrivalsBlock, { arrivals: [...Array(12)].map((_, i) => arrival(i)) }));
  assert.ok(html.includes("Role 9"), "the tenth row is shown");
  assert.ok(!html.includes("Role 10"), "the eleventh is not, until asked for");
});

test("nothing to show renders nothing: the page's own line says 'nothing new'", () => {
  assert.equal(render(createElement(ArrivalsBlock, { arrivals: [] })), "");
});
