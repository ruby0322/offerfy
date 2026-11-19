import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import OnboardingCard from "./OnboardingCard";
import type { FactsState, JobPreference } from "@/lib/jobs";

/**
 * The card's first screen, which is the first screen of the whole feature.
 *
 * A static render, so no effect runs and nothing is fetched: what is asserted
 * is the sentence a person meets before any of that, which is where the false
 * claim lived -- "We read your resume and filled these in" over no chips.
 */

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
      // eslint-disable-next-line react/no-children-prop
      createElement(NextIntlClientProvider, {
        locale: "en",
        messages: en,
        timeZone: "Asia/Taipei",
        children: node,
      }),
    ),
  );
}

function seed(over: Partial<JobPreference> = {}): JobPreference {
  return {
    target_functions: [],
    target_locations: [],
    target_seniority: null,
    remote_required: null,
    activity: "actively_looking",
    updated_at: null,
    facts_state: "fresh" as FactsState,
    ...over,
  };
}

function card(pref: JobPreference): string {
  return render(
    createElement(OnboardingCard, { pref, onDone: () => Promise.resolve() }),
  );
}

const LEAD = en.dashboard.onboardLead;
const BLANK = en.dashboard.onboardLeadBlank;

test("a filled seed is the only thing that may say the resume was read", () => {
  const html = card(seed({ target_functions: ["backend"], target_locations: ["tw"] }));
  assert.ok(html.includes(LEAD));
  assert.ok(!html.includes(BLANK));
});

test("a resume too thin to seed anything does not claim it was read", () => {
  // Facts exist, so nothing will be built; they just said nothing useful.
  const html = card(seed());
  assert.ok(html.includes(BLANK));
  assert.ok(!html.includes(LEAD), "no seed, no claim of one");
});

test("while the resume is being read, neither claim is made yet", () => {
  // facts_state missing is the default new account: the card is about to
  // build the facts, so "we read your resume" and "we could not fill these
  // in" are both unknown.
  const html = card(seed({ facts_state: "missing" }));
  assert.ok(html.includes(en.dashboard.onboardReading));
  assert.ok(!html.includes(LEAD) && !html.includes(BLANK));
  // And no dead Save, and no empty chip rows, sitting under it while that
  // happens: the questions arrive once there is something in them.
  assert.ok(!html.includes(en.dashboard.onboardSave));
  assert.ok(!html.includes(en.dashboard.onboardQFunction));
});

test("a seniority on its own is a seed", () => {
  const html = card(seed({ target_seniority: "senior" }));
  assert.ok(html.includes(LEAD) && !html.includes(BLANK));
});
