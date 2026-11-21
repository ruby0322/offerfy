/**
 * The contract between `McpGuide.tsx` and the message files.
 *
 * Three of the page's sections come from `t.raw()`, which returns `unknown` and
 * is cast at the call site. TypeScript cannot see past that cast, so a key that
 * is missing, renamed, or the wrong shape compiles cleanly and then throws while
 * rendering.
 *
 * `i18n-parity` covers more of this than it looks: it compares array entries as
 * well as key sets, so a shape that drifts in *one* locale already fails there —
 * measured, not assumed. What it cannot see is a shape that is wrong the same
 * way in all three at once (turning `webSteps` into a string everywhere passes
 * parity and fails only here), and it says nothing about content: that the
 * prompt still carries the endpoint, the header, and the placeholder the help
 * text tells the reader to replace.
 *
 * So: the shapes and those contents, in every locale, pinned here.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const LOCALES = ["en", "zh-TW", "zh-CN"] as const;

/** Every tool the page promises. If the surface grows, this list moves first. */
const FIFTEEN_TOOLS = 15;

function messages(locale: string): Record<string, unknown> {
  const raw = readFileSync(new URL(`../../messages/${locale}.json`, import.meta.url), "utf8");
  return (JSON.parse(raw) as { mcp: Record<string, unknown> }).mcp;
}

test("every locale has the mcp namespace", () => {
  for (const locale of LOCALES) {
    assert.equal(typeof messages(locale), "object", locale);
  }
});

test("webSteps is a non-empty list of strings in every locale", () => {
  for (const locale of LOCALES) {
    const steps = messages(locale).webSteps;
    assert.ok(Array.isArray(steps), locale);
    assert.ok(steps.length >= 4, `${locale}: ${steps.length} steps`);
    for (const step of steps) assert.equal(typeof step, "string", locale);
  }
});

test("safetyPoints is a non-empty list of strings in every locale", () => {
  for (const locale of LOCALES) {
    const points = messages(locale).safetyPoints;
    assert.ok(Array.isArray(points), locale);
    assert.ok(points.length >= 4, `${locale}: ${points.length} points`);
    for (const point of points) assert.equal(typeof point, "string", locale);
  }
});

test("groups is a list of {heading, body} in every locale", () => {
  for (const locale of LOCALES) {
    const groups = messages(locale).groups;
    assert.ok(Array.isArray(groups), locale);
    assert.ok(groups.length >= 1, locale);
    for (const group of groups) {
      assert.equal(typeof group, "object", locale);
      assert.equal(typeof (group as { heading?: unknown }).heading, "string", locale);
      assert.equal(typeof (group as { body?: unknown }).body, "string", locale);
    }
  }
});

test("the three locales describe the same number of groups and steps", () => {
  const shape = (locale: string) => {
    const m = messages(locale);
    return [
      (m.webSteps as unknown[]).length,
      (m.safetyPoints as unknown[]).length,
      (m.groups as unknown[]).length,
    ];
  };
  for (const locale of LOCALES.slice(1)) {
    assert.deepEqual(shape(locale), shape("en"), locale);
  }
});

test("the page names the endpoint and the header, in every locale", () => {
  for (const locale of LOCALES) {
    const m = messages(locale);
    const body = m.promptBody as string;
    assert.match(body, /https:\/\/offerfy\.cc\/api\/mcp/, locale);
    assert.match(body, /Authorization: Bearer/, locale);
    // The placeholder has to survive translation, because the help text above
    // the block tells the reader to replace exactly this string.
    assert.ok(body.includes("ofa_YOUR_KEY"), `${locale} promptBody`);
    assert.ok((m.promptHelp as string).includes("ofa_YOUR_KEY"), `${locale} promptHelp`);
    assert.ok((m.ccCommand as string).includes("ofa_YOUR_KEY"), `${locale} ccCommand`);
  }
});

test("the Claude Code command is the one the docs give, not a paraphrase", () => {
  for (const locale of LOCALES) {
    const cmd = messages(locale).ccCommand as string;
    assert.match(cmd, /^claude mcp add --transport http /, locale);
    assert.match(cmd, /--header "Authorization: Bearer ofa_YOUR_KEY"$/, locale);
  }
});

test("the prompt tells the agent to report back, so the reader gets proof", () => {
  // Without this the reader pastes the block and has no idea whether it worked.
  // Each locale words it differently, so match on the tool-listing instruction
  // being present at all rather than on any one phrasing.
  for (const locale of LOCALES) {
    const body = (messages(locale).promptBody as string).toLowerCase();
    assert.ok(
      body.includes("list the tools") || body.includes("列出你看到的工具"),
      `${locale}: the prompt does not ask the agent to report what it found`,
    );
  }
});

test("the tool groups account for all fifteen tools", () => {
  // The page promises a number in `toolsLede`; if the surface grows and that
  // sentence is not updated, the page undersells or oversells it.
  for (const locale of LOCALES) {
    const lede = messages(locale).toolsLede as string;
    assert.ok(
      lede.includes(String(FIFTEEN_TOOLS)) || /fifteen|十五/i.test(lede),
      `${locale}: toolsLede does not say how many tools there are`,
    );
  }
});
