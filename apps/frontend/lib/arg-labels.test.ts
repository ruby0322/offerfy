import assert from "node:assert/strict";
import test from "node:test";

import en from "../messages/en.json";
import zhCN from "../messages/zh-CN.json";
import zhTW from "../messages/zh-TW.json";
import { ARG_LABEL_MESSAGE_IDS, argLabelId } from "./arg-labels";

/**
 * Every argument key the backend's pinned fixtures project (`parse_projection` over
 * apps/backend/tests/fixtures/typst/*.typ, `kind == "arg"`). The union over the six
 * fixtures is these 18; `synthetic-tier-c` projects none.
 */
const FIXTURE_KEYS = [
  "accent-color",
  "author",
  "company",
  "dates",
  "degree",
  "email",
  "end-date",
  "github",
  "institution",
  "lang",
  "location",
  "name",
  "paper",
  "personal-site",
  "role",
  "start-date",
  "title",
  "url",
];

test("every key a pinned fixture projects has a label", () => {
  for (const key of FIXTURE_KEYS) {
    assert.notEqual(argLabelId(key), null, key);
  }
});

test("the keys of the other registered emitters have labels too", () => {
  for (const key of ["organization", "date", "contacts", "technologies", "text-color"]) {
    assert.notEqual(argLabelId(key), null, key);
  }
});

test("every mapped id is a message in all three locales, and not the raw id", () => {
  for (const [name, messages] of [
    ["en", en],
    ["zh-TW", zhTW],
    ["zh-CN", zhCN],
  ] as const) {
    const editor = messages.editor as Record<string, string>;
    for (const id of ARG_LABEL_MESSAGE_IDS) {
      assert.equal(typeof editor[id], "string", `${name}: ${id}`);
      assert.ok(editor[id].length > 0, `${name}: ${id} is empty`);
    }
  }
});

test("two keys never share a message id", () => {
  assert.equal(new Set(ARG_LABEL_MESSAGE_IDS).size, ARG_LABEL_MESSAGE_IDS.length);
});

test("a key no template has used yet maps to nothing, so the caller shows the key", () => {
  assert.equal(argLabelId("hobby"), null);
  assert.equal(argLabelId(""), null);
  assert.equal(argLabelId(null), null);
  assert.equal(argLabelId(undefined), null);
});

test("a key that is an Object.prototype member is not a label", () => {
  for (const key of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
    assert.equal(argLabelId(key), null, key);
  }
});

test("the longest zh-TW label stays within the label cell", () => {
  // The cell is `min-w-20` (80px) at `text-xs` (12px): 6 CJK characters is the most that
  // fits without widening it. Latin text is narrower than that per character.
  const editor = zhTW.editor as Record<string, string>;
  for (const id of ARG_LABEL_MESSAGE_IDS) {
    assert.ok(Array.from(editor[id]).length <= 6, `${id}: ${editor[id]}`);
  }
});
