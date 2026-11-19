import assert from "node:assert/strict";
import test from "node:test";

import { getPreviewPages, parsePreviewPages } from "./api";
import { bandsForUnits, bandsOnPage } from "./preview-highlight";

// Payloads are the JSON text the backend sends (`PreviewPages` in schemas.py, snake_case
// keys, `page` 0-based and parallel to `pages`), parsed the way `apiJson` parses a body.
const wire = (bands: string, extra = '"pages": ["<svg/>"], "text_layers": []') =>
  parsePreviewPages(JSON.parse(`{ ${extra}, "bands": ${bands} }`));

test("the backend's own shape comes through key for key", () => {
  const out = wire(`{
    "u1": [{"page": 0, "y": 72.5, "height": 11.0}, {"page": 0, "y": 84.0, "height": 11.0}],
    "u2": [{"page": 1, "y": 30, "height": 9}]
  }`);
  assert.deepEqual(out.bands, {
    u1: [
      { page: 0, y: 72.5, height: 11 },
      { page: 0, y: 84, height: 11 },
    ],
    u2: [{ page: 1, y: 30, height: 9 }],
  });
});

test("page is 0-based: page 0 draws on the first page, not the second", () => {
  const out = wire(`{"u1": [{"page": 0, "y": 10, "height": 5}]}`);
  const found = bandsForUnits(out.bands, ["u1"]);
  assert.equal(bandsOnPage(found, 0).length, 1);
  assert.equal(bandsOnPage(found, 1).length, 0);
});

test("a band past the last page draws on no page, and is not clamped onto the last", () => {
  const out = wire(`{"u1": [{"page": 5, "y": 10, "height": 5}]}`);
  const found = bandsForUnits(out.bands, ["u1"]);
  assert.equal(found.length, 1, "parsing does not know the page count");
  // EditorPreview asks for index 0..pages.length-1 and nothing else
  const pages = JSON.parse('{"pages": ["<svg/>", "<svg/>"]}').pages as string[];
  assert.equal(pages.length, 2);
  for (let index = 0; index < pages.length; index += 1) {
    assert.deepEqual(bandsOnPage(found, index), [], `page ${index}`);
  }
  // and the band is not lost to the parser: it is on page 5, which this preview lacks
  assert.equal(bandsOnPage(found, 5).length, 1);
});

test("bands absent, null, an array or a string is an empty map", () => {
  assert.deepEqual(parsePreviewPages(JSON.parse('{"pages": ["<svg/>"]}')).bands, {});
  assert.deepEqual(wire("null").bands, {});
  assert.deepEqual(wire("[]").bands, {});
  assert.deepEqual(wire('"u1"').bands, {});
  assert.deepEqual(wire("{}").bands, {});
});

test("a body that is not an object has no bands and does not throw", () => {
  for (const text of ["null", "[]", '"x"', "7"]) {
    assert.deepEqual(parsePreviewPages(JSON.parse(text)).bands, {}, text);
  }
});

test("a unit whose value is not an array is dropped; its neighbours stay", () => {
  const out = wire(`{
    "u1": {"page": 0, "y": 1, "height": 1},
    "u2": "oops",
    "u3": [{"page": 0, "y": 2, "height": 3}]
  }`);
  assert.deepEqual(out.bands, { u3: [{ page: 0, y: 2, height: 3 }] });
});

test("a bad band is dropped alone: the unit's other bands survive", () => {
  const out = wire(`{"u1": [
    {"page": 0, "y": 10, "height": 5},
    {"y": 20, "height": 5},
    {"page": 0, "height": 5},
    {"page": 0, "y": 20},
    {"page": 0, "y": "20", "height": 5},
    {"page": 0, "y": 20, "height": null},
    {"page": -1, "y": 20, "height": 5},
    {"page": 0.5, "y": 20, "height": 5},
    {"page": "0", "y": 20, "height": 5},
    null,
    7,
    {"page": 0, "y": 30, "height": 5}
  ]}`);
  assert.deepEqual(out.bands, {
    u1: [
      { page: 0, y: 10, height: 5 },
      { page: 0, y: 30, height: 5 },
    ],
  });
});

test("a unit left with no valid band is not in the map at all", () => {
  const out = wire(`{"u1": [{"page": -2, "y": 1, "height": 1}], "u2": []}`);
  assert.deepEqual(out.bands, {});
});

test("the bands are asked for only when the caller is going to draw them", async () => {
  // They cost the server a `typst eval` and two projection parses, and the shell's
  // autosave calls this endpoint 400ms after every keystroke burst in the Typst tab --
  // where nobody is highlighting anything and the band cache misses every time, the
  // source having just changed. The design: 「只在欄位分頁開著的時候跑」.
  const asked: string[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = ((input: string) => {
    asked.push(String(input));
    return Promise.resolve(
      new Response('{"pages": [], "text_layers": [], "bands": {}}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }) as typeof globalThis.fetch;
  try {
    await getPreviewPages("r1");
    await getPreviewPages("r1", false);
    await getPreviewPages("r1", true);
  } finally {
    globalThis.fetch = real;
  }
  assert.deepEqual(asked, [
    "/api/v1/resumes/r1/preview",
    "/api/v1/resumes/r1/preview",
    "/api/v1/resumes/r1/preview?bands=1",
  ]);
});
