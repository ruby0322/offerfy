import assert from "node:assert/strict";
import test from "node:test";

import { EDITOR_TABS, normalizeEditorTab } from "./editor-tab";

test("every tab in the strip normalises to itself", () => {
  assert.ok(EDITOR_TABS.includes("fields"));
  for (const tab of EDITOR_TABS) assert.equal(normalizeEditorTab(tab), tab);
});

test("a tab name this build does not have falls back to the source tab", () => {
  // "preview" is what sessionStorage still holds from before the preview left the
  // strip; "fields" is what it would hold after a rollback of this build. Both must
  // select a real tab -- an unknown value selects no trigger and renders nothing.
  for (const stored of ["preview", "fields-2", "", " typst", "FIELDS", null, undefined]) {
    assert.equal(normalizeEditorTab(stored), "typst", `kept ${JSON.stringify(stored)}`);
  }
});
