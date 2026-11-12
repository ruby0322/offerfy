import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import ApplicationList from "./ApplicationList";
import EmployerViews from "./EmployerViews";
import MatchCountCard from "./MatchCountCard";
import ResumeHealthCard from "./ResumeHealthCard";

// Review Focus 1: a brand-new signed-in person (no preference, no facts, no
// applications, no tailors) sees all five sections empty at once. Each
// component must say so in its own words, not render blank or throw.
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
      createElement(NextIntlClientProvider, {
        locale: "en",
        messages: en,
        children: node,
      }),
    ),
  );
}

const d = en.dashboard;

test("MatchCountCard with no preference says what to do next, not a count", () => {
  const html = render(createElement(MatchCountCard, { count: 0, hasPreference: false }));
  assert.ok(html.includes(d.matchEmpty), html);
  assert.ok(html.includes(d.headingMatches), html);
});

test("ApplicationList with no applications shows its empty message", () => {
  const html = render(createElement(ApplicationList, { initial: [] }));
  assert.ok(html.includes(d.applicationsEmpty), html);
  assert.ok(html.includes('data-testid="dashboard-applications-empty"'), html);
});

test("ResumeHealthCard with no master resume shows the empty state and a way in", () => {
  const html = render(
    createElement(ResumeHealthCard, {
      health: {
        has_master: false,
        published: false,
        facts_state: "missing",
        matching_revision_current: false,
        needs_rebuild: false,
      },
      masterId: null,
      onRebuilt() {},
    }),
  );
  assert.ok(html.includes(d.healthEmpty), html);
  assert.ok(/href="\/en\/new"/.test(html), html);
  assert.ok(!html.includes(d.rebuildNow), html);
});

test("EmployerViews with no disclosures shows its empty message", () => {
  const html = render(
    createElement(EmployerViews, {
      views: { disclosure_count: 0, last_disclosed_at: null },
    }),
  );
  assert.ok(html.includes(d.employerViewsEmpty), html);
});
