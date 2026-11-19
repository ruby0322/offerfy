import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import en from "@/messages/en.json";
import MatchesBlock from "./MatchesBlock";

// The standing list's empty sentence depends on whether the person has said
// what they want: "tell us" to someone who has is the contradiction this pins.
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

test("an empty standing list with a saved preference says nothing matches", () => {
  const html = render(
    createElement(MatchesBlock, {
      digest: null,
      matches: [],
      matchCount: 0,
      hasPreference: true,
    }),
  );
  assert.ok(html.includes(d.matchNone), html);
  assert.ok(!html.includes(d.matchEmpty), html);
});

test("an empty standing list without a preference points at the card", () => {
  const html = render(
    createElement(MatchesBlock, {
      digest: null,
      matches: [],
      matchCount: 0,
      hasPreference: false,
    }),
  );
  assert.ok(html.includes(d.matchEmpty), html);
  assert.ok(!html.includes(d.matchNone), html);
});
