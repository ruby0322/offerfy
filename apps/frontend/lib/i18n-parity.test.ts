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

test("import card keys exist in every locale", () => {
  for (const m of [en, zhTW, zhCN]) assert.ok(m.editor.importTitle);
});
