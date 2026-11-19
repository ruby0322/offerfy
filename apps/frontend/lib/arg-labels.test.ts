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
  for (const key of [
    "organization",
    "date",
    "contacts",
    "technologies",
    "text-color",
    "live-url",
    "repo-url",
    "gpa",
    "tech-used",
  ]) {
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

/**
 * Roughly how wide a label renders at `text-xs` (12px), in px. A CJK glyph is full-width
 * -- one em -- while Latin averages a little over half that, so a flat character count
 * is the wrong measure for a label like "LinkedIn": 8 characters, narrower than 5 of 字體.
 */
function labelWidthPx(text: string): number {
  let px = 0;
  for (const ch of text) {
    // CJK, fullwidth forms, kana and the CJK punctuation that shares their advance
    px += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/u.test(ch)
      ? 12
      : 6.6;
  }
  return px;
}

test("labelWidthPx charges a CJK glyph a full em and Latin about half", () => {
  // Pins the measure itself, so the cell test below cannot pass by measuring nothing.
  assert.equal(labelWidthPx("字體"), 24);
  assert.equal(labelWidthPx("作品集"), 36);
  assert.ok(labelWidthPx("LinkedIn") < labelWidthPx("紙張大小五"));
  assert.ok(labelWidthPx("七個中文字元寬") > 80, "7 CJK characters must overflow 80px");
});

test("the longest zh-TW label stays within the label cell", () => {
  // The cell is `min-w-20` -- 80px -- and a label wider than that widens it, pushing the
  // input along. 6 CJK characters (72px) fit; 7 (84px) do not.
  const editor = zhTW.editor as Record<string, string>;
  for (const id of ARG_LABEL_MESSAGE_IDS) {
    const width = labelWidthPx(editor[id]);
    assert.ok(width <= 80, `${id}: ${editor[id]} is ~${width}px, over the 80px cell`);
  }
});
