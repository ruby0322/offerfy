import assert from "node:assert/strict";
import test from "node:test";
import en from "../messages/en.json";
import zhCN from "../messages/zh-CN.json";
import zhTW from "../messages/zh-TW.json";

/**
 * A missing message key throws at render time rather than falling back
 * (see AGENTS.md §6), so the three locale files must carry exactly the
 * same keys. This is the only thing standing between "someone added a key
 * to one locale" and a page that 500s for everyone reading the other two.
 */
function flatten(value: object, prefix = ""): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child !== null && typeof child === "object" && !Array.isArray(child)) {
      Object.assign(out, flatten(child as object, path));
    } else {
      out[path] = child;
    }
  }
  return out;
}

const keys = (o: object) => Object.keys(flatten(o)).sort();

test("every locale has the same key set", () => {
  assert.deepEqual(keys(zhTW), keys(en));
  assert.deepEqual(keys(zhCN), keys(en));
});

/**
 * `flatten` above stops at an array, so `about.paragraphs` is one key in every
 * locale whether it holds three entries or five. That is how en shipped three
 * About paragraphs against zh's four, and three Contact paragraphs against
 * zh's five, for weeks, with this suite green: the test that would have caught
 * the gap could not see it, and closing the hole fails until the content
 * exists. `flattenIndexed` descends into arrays, so a missing entry is a
 * failing key rather than an invisible one.
 */
function flattenIndexed(value: object, prefix = ""): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child !== null && typeof child === "object") {
      Object.assign(out, flattenIndexed(child as object, path));
    } else {
      out[path] = child;
    }
  }
  return out;
}

const indexedKeys = (o: object) => Object.keys(flattenIndexed(o)).sort();

test("every locale has the same array entries, not just the same arrays", () => {
  assert.deepEqual(indexedKeys(zhTW), indexedKeys(en));
  assert.deepEqual(indexedKeys(zhCN), indexedKeys(en));
});

test("import card keys exist in every locale", () => {
  for (const m of [en, zhTW, zhCN]) assert.ok(m.editor.importTitle);
});
