import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import type { MatchArrival } from "@/lib/dashboard";
import type { JobListItem } from "@/lib/jobs";
import MatchesBlock from "./MatchesBlock";

// Two things are pinned here. The empty sentence depends on whether the person
// has said what they want -- "tell us" to someone who has is a contradiction --
// and the right column's heading has to say which list it is actually showing,
// because it draws on arrivals or on the standing set depending on the data.
const router = {
  push() {},
  replace() {},
  back() {},
  forward() {},
  refresh() {},
  prefetch() {},
};

function render(node: ReactElement): string {
  // NextIntlClientProvider types `children` as a prop; built outside the call
  // so react/no-children-prop does not flag a literal.
  const intl = { locale: "en" as const, messages: en, children: node };
  return renderToStaticMarkup(
    createElement(AppRouterContext.Provider, { value: router as never }, createElement(NextIntlClientProvider, intl)),
  );
}

const d = en.dashboard;

// MatchRow renders this wrapper only when there is a score or a tier to show,
// so its absence is how "no score slot at all" is asserted. Checking for a
// bare number instead would catch the card's own match count.
const SCORE_SLOT = "min-w-8 shrink-0 text-right";

// A quiet week: the left column renders its plain sentence rather than
// SamplePick, which carries a score slot of its own and would otherwise be
// what the SCORE_SLOT assertions below are measuring.
const quietWeek = {
  status: "empty" as const,
  built_at: "2026-11-17T05:00:00Z",
  opened_at: null,
  candidate_count: 0,
  items: [],
};

function arrival(over: Partial<MatchArrival> = {}): MatchArrival {
  return {
    job_id: "a1",
    job_title: "Backend Engineer",
    company: "Acme",
    apply_url: "https://example.com/a1",
    job_is_active: true,
    location: "Taipei",
    tier: "Strong fit",
    fit_score: 0.9,
    reason: null,
    gap: null,
    scored_at: "2026-11-19T00:00:00Z",
    ...over,
  };
}

function listing(over: Partial<JobListItem> = {}): JobListItem {
  return {
    id: "j1",
    source: "greenhouse",
    company: "Globex",
    title: "Platform Engineer",
    location: "Remote",
    remote: true,
    apply_url: "https://example.com/j1",
    source_url: "https://example.com/s1",
    posted_at: null,
    last_seen_at: "2026-11-18T00:00:00Z",
    is_active: true,
    ...over,
  };
}

const base = { digest: null, matchCount: 0, hasPreference: true };

test("an empty right column with a saved preference says nothing matches", () => {
  const html = render(
    createElement(MatchesBlock, { ...base, arrivals: [], matches: [] }),
  );
  assert.ok(html.includes(d.matchNone), html);
  assert.ok(!html.includes(d.matchEmpty), html);
});

test("an empty right column without a preference points at the card", () => {
  const html = render(
    createElement(MatchesBlock, {
      ...base,
      hasPreference: false,
      arrivals: [],
      matches: [],
    }),
  );
  assert.ok(html.includes(d.matchEmpty), html);
  assert.ok(!html.includes(d.matchNone), html);
});

test("arrivals fill the right column and the heading calls them new", () => {
  const html = render(
    createElement(MatchesBlock, {
      ...base,
      arrivals: [arrival()],
      matches: [listing()],
    }),
  );
  assert.ok(html.includes("Backend Engineer"), html);
  assert.ok(!html.includes("Platform Engineer"), html);
  assert.ok(html.includes(d.freshHeading), html);
  assert.ok(!html.includes(d.standingHeading), html);
});

test("with nothing new the standing set fills it, under its own heading", () => {
  // The fallback is what stops the column going blank for someone who opens
  // the dashboard twice in an hour, and the heading must not call it new.
  const html = render(
    createElement(MatchesBlock, { ...base, arrivals: [], matches: [listing()] }),
  );
  assert.ok(html.includes("Platform Engineer"), html);
  assert.ok(html.includes(d.standingHeading), html);
  assert.ok(!html.includes(d.freshHeading), html);
});

test("a scored arrival shows its score in the right column", () => {
  // The columns no longer mean "scored left, unscored right" -- the sweep
  // scores everything -- so hiding a number we already hold would throw a
  // real signal away.
  const html = render(
    createElement(MatchesBlock, {
      ...base,
      arrivals: [arrival({ fit_score: 0.9 })],
      matches: [],
    }),
  );
  assert.ok(html.includes("90"), html);
});

test("an arrival the sweep has not reached shows no score", () => {
  // A blank is the honest answer for a posting nobody has judged; handing it
  // a number would be inventing one.
  const html = render(
    createElement(MatchesBlock, {
      ...base,
      digest: quietWeek,
      arrivals: [arrival({ fit_score: null, tier: null, scored_at: null })],
      matches: [],
    }),
  );
  assert.ok(html.includes("Backend Engineer"), html);
  assert.ok(!html.includes(SCORE_SLOT), html);
});

test("the standing fallback carries no score at all", () => {
  // `JobListItem` is a posting list, not a judgement: there is nothing to show.
  const html = render(
    createElement(MatchesBlock, {
      ...base,
      digest: quietWeek,
      arrivals: [],
      matches: [listing()],
    }),
  );
  assert.ok(html.includes("Platform Engineer"), html);
  assert.ok(!html.includes(SCORE_SLOT), html);
});
