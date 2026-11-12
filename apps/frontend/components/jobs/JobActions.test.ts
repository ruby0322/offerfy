import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import JobActions from "./JobActions";

// A static render is the pre-hydration HTML: what a crawler and a visitor
// without JavaScript get on the public, indexed /jobs/[id] page.
test("the pre-hydration HTML carries the apply anchor, enabled", () => {
  const router = {
    push() {},
    replace() {},
    back() {},
    forward() {},
    refresh() {},
    prefetch() {},
  };
  const html = renderToStaticMarkup(
    createElement(
      AppRouterContext.Provider,
      { value: router as never },
      createElement(
        NextIntlClientProvider,
        {
          locale: "en",
          messages: { jobs: {} },
          children: createElement(JobActions, {
            jobId: "j1",
            applyUrl: "https://boards.example.com/apply/1",
            title: "Engineer",
            company: "Acme",
            applyLabel: "Apply on Greenhouse",
          }),
        },
      ),
    ),
  );
  const anchor = html.match(/<a [^>]*>/)?.[0] ?? "";
  assert.match(anchor, /href="https:\/\/boards\.example\.com\/apply\/1"/);
  assert.match(anchor, /target="_blank"/);
  assert.doesNotMatch(anchor, /disabled|aria-disabled/);
  assert.match(html, /Apply on Greenhouse/);
});
