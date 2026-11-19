import assert from "node:assert/strict";
import test from "node:test";

import type { PreviewBand } from "./api";
import {
  armedUnitIds,
  bandRect,
  bandsForUnits,
  bandsNeedFetch,
  bandsOnPage,
  canCenter,
  scrollTopToCenter,
} from "./preview-highlight";
import { blockKey, sectionKey } from "./structural-controls";
import type {
  Projection,
  ProjectionBlock,
  ProjectionSection,
  ProjectionUnit,
} from "./structured-fields";

const band = (page: number, y: number, height: number): PreviewBand => ({ page, y, height });

// --- geometry --------------------------------------------------------------------------

test("a band is a share of the page width, the way the text layer lays out", () => {
  // unit = 100 / 200 = 0.5, so y 40 -> 20cqw and height 10 -> 5cqw
  assert.deepEqual(bandRect(band(0, 40, 10), 200), { top: "20cqw", height: "5cqw" });
  // unit = 100 / 400 = 0.25, so y 10 -> 2.5cqw and height 6 -> 1.5cqw
  assert.deepEqual(bandRect(band(0, 10, 6), 400), { top: "2.5cqw", height: "1.5cqw" });
});

test("the same band on a page twice as wide is half the share", () => {
  const narrow = bandRect(band(0, 40, 10), 200);
  const wide = bandRect(band(0, 40, 10), 400);
  assert.deepEqual(narrow, { top: "20cqw", height: "5cqw" });
  assert.deepEqual(wide, { top: "10cqw", height: "2.5cqw" });
});

test("a page with no usable width draws nothing rather than dividing by it", () => {
  for (const width of [0, -612, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(bandRect(band(0, 40, 10), width), null, `width ${width}`);
  }
});

test("a band that is not a positive finite row draws nothing", () => {
  assert.equal(bandRect(band(0, 40, 0), 200), null);
  assert.equal(bandRect(band(0, 40, -3), 200), null);
  assert.equal(bandRect(band(0, Number.NaN, 10), 200), null);
  assert.equal(bandRect(band(0, 40, Number.POSITIVE_INFINITY), 200), null);
});

// --- which bands ------------------------------------------------------------------------

test("bands on a page are that page's, and a page the preview lacks gets none", () => {
  const all = [band(0, 1, 1), band(1, 2, 1), band(1, 3, 1)];
  assert.deepEqual(bandsOnPage(all, 0), [band(0, 1, 1)]);
  assert.deepEqual(bandsOnPage(all, 1), [band(1, 2, 1), band(1, 3, 1)]);
  assert.deepEqual(bandsOnPage(all, 2), []);
  assert.deepEqual(bandsOnPage(all, -1), []);
  assert.deepEqual(bandsOnPage([], 0), []);
});

test("a wrapped unit keeps every one of its bands, in the order given", () => {
  const map = { u1: [band(0, 10, 5), band(0, 15, 5)], u2: [band(0, 30, 5)] };
  assert.deepEqual(bandsForUnits(map, ["u1"]), [band(0, 10, 5), band(0, 15, 5)]);
  assert.deepEqual(bandsForUnits(map, ["u2", "u1"]), [
    band(0, 30, 5),
    band(0, 10, 5),
    band(0, 15, 5),
  ]);
});

test("an empty map, or a unit missing from a non-empty one, adds nothing", () => {
  assert.deepEqual(bandsForUnits({}, ["u1", "u2"]), []);
  assert.deepEqual(bandsForUnits({ u1: [band(0, 10, 5)] }, ["u9"]), []);
  // the present unit still draws when its neighbour is missing
  assert.deepEqual(bandsForUnits({ u1: [band(0, 10, 5)] }, ["u9", "u1"]), [band(0, 10, 5)]);
  assert.deepEqual(bandsForUnits({ u1: [band(0, 10, 5)] }, []), []);
});

test("a unit id that is also an Object.prototype name is not a band list", () => {
  assert.deepEqual(bandsForUnits({}, ["constructor", "toString", "__proto__"]), []);
});

test("a unit named twice is drawn once", () => {
  assert.deepEqual(bandsForUnits({ u1: [band(0, 10, 5)] }, ["u1", "u1"]), [band(0, 10, 5)]);
});

// --- the armed block --------------------------------------------------------------------

const u = (id: string, value: string, over: Partial<ProjectionUnit> = {}): ProjectionUnit => ({
  id,
  kind: "line",
  value,
  editable: true,
  key: null,
  style: false,
  role: "bullet",
  marks: [],
  opaque: [],
  ...over,
});

const b = (id: string, over: Partial<ProjectionBlock> = {}): ProjectionBlock => ({
  id,
  kind: "comp",
  name: null,
  args: [],
  unit: null,
  bullets: [],
  bodyUnits: [],
  ...over,
});

const s = (
  id: string,
  titleUnit: string | null,
  blocks: ProjectionBlock[],
): ProjectionSection => ({ id, titleUnit, blocks });

function proj(sections: ProjectionSection[], units: ProjectionUnit[]): Projection {
  return {
    hash: "h1",
    tier: "A",
    sections,
    units: Object.fromEntries(units.map((x) => [x.id, x])),
    report: { structures: {}, skippedArgs: {}, lints: [] },
  };
}

// s1 "Work": b1 is an entry (two args, a body line, two bullets); b4 is a bare line.
function fixture(extra: ProjectionUnit[] = []): Projection {
  return proj(
    [
      s("s1", "u1", [
        b("b1", {
          args: ["a1", "a2"],
          bodyUnits: ["u3"],
          bullets: [b("b2", { unit: "u4" }), b("b3", { unit: "u5" })],
        }),
        b("b4", { kind: "line", unit: "u6" }),
      ]),
    ],
    [
      u("u1", "Work", { role: "heading" }),
      u("a1", "Acme", { kind: "arg", key: "title", role: null }),
      u("a2", "2020", { kind: "arg", key: "date", role: null }),
      u("u3", "Led a team"),
      u("u4", "Cut costs"),
      u("u5", "Shipped it"),
      u("u6", "Elsewhere"),
      ...extra,
    ],
  );
}

const armOf = (p: Projection, id: string) => ({ target: id, key: blockKey(p, id) as string });

test("an armed block names every unit in it and none of its neighbours", () => {
  const p = fixture();
  const ids = armedUnitIds(p, armOf(p, "b1"));
  assert.deepEqual([...ids].sort(), ["a1", "a2", "u3", "u4", "u5"]);
  assert.ok(!ids.includes("u6"), "the next block is not taken");
  assert.ok(!ids.includes("u1"), "the section heading is not taken");
});

test("an armed bullet takes that bullet and not its sibling or its parent", () => {
  const p = fixture();
  assert.deepEqual(armedUnitIds(p, armOf(p, "b2")), ["u4"]);
});

test("an armed section takes its title and everything under it", () => {
  const p = fixture();
  const key = sectionKey(p, "s1") as string;
  const ids = armedUnitIds(p, { target: "s1", key });
  assert.deepEqual([...ids].sort(), ["a1", "a2", "u1", "u3", "u4", "u5", "u6"]);
});

test("nothing armed, or no projection yet, is nothing to draw", () => {
  const p = fixture();
  assert.deepEqual(armedUnitIds(p, null), []);
  assert.deepEqual(armedUnitIds(null, { target: "b1", key: "k" }), []);
  assert.deepEqual(armedUnitIds(null, null), []);
});

test("an arm whose target is gone draws nothing", () => {
  const p = fixture();
  assert.deepEqual(armedUnitIds(p, { target: "b99", key: "anything" }), []);
});

test("an arm whose key no longer matches draws nothing: ids are positional", () => {
  const armedAgainst = fixture();
  const arm = armOf(armedAgainst, "b1");
  // a structural edit elsewhere added a unit: b1 is still an id in the document, but
  // `blockKey` carries the document's size, so the arm was made against another document
  const after = fixture([u("u7", "A line a copy added")]);
  assert.ok(findKeyDiffers(armedAgainst, after, "b1"), "fixture must actually change the key");
  assert.ok(armedUnitIds(armedAgainst, arm).length > 0, "the arm is live against its own document");
  assert.deepEqual(armedUnitIds(after, arm), []);
});

test("a block id does not arm a section that happens to share its key text", () => {
  const p = fixture();
  assert.deepEqual(armedUnitIds(p, { target: "s1", key: blockKey(p, "b1") as string }), []);
});

function findKeyDiffers(a: Projection, b2: Projection, id: string): boolean {
  return blockKey(a, id) !== blockKey(b2, id);
}

// --- which source the bands describe ---------------------------------------------------

test("bands of the previous source are fetched again, however many there are", () => {
  // The defect: `/edit` answered with no pages, `source` moved to "B", and the bands on
  // screen (non-empty) still described "A". Emptiness said "have some"; provenance says
  // "not of this one".
  assert.equal(bandsNeedFetch({ describes: "A", askedFor: null, source: "B" }), true);
  assert.equal(bandsNeedFetch({ describes: "A", askedFor: "A", source: "B" }), true);
});

test("bands of unknown provenance are fetched", () => {
  assert.equal(bandsNeedFetch({ describes: null, askedFor: null, source: "B" }), true);
});

test("bands that describe this source are kept, empty or not", () => {
  // `describes` says nothing about how many bands there are: a document with no editable
  // line has none, and that is the answer for it.
  assert.equal(bandsNeedFetch({ describes: "B", askedFor: null, source: "B" }), false);
});

test("a source already asked for is not asked again", () => {
  assert.equal(bandsNeedFetch({ describes: null, askedFor: "B", source: "B" }), false);
  assert.equal(bandsNeedFetch({ describes: "A", askedFor: "B", source: "B" }), false);
});

// --- centring the highlight in the scroll container -------------------------------------

// A 400px-high viewport over 2000px of content, whose top edge sits at 100px on screen.
// Its scrollable range is therefore [0, 2000 - 400] = [0, 1600], and the middle of the
// viewport is 200px below its top.
const view = {
  containerTop: 100,
  scrollTop: 0,
  clientHeight: 400,
  scrollHeight: 2000,
};

test("a band in the middle of the document is centred, with no clamp involved", () => {
  // on screen at 700 -> 700 - 100 + 0 = 600 into the content; centre 600 + 20/2 = 610;
  // target 610 - 400/2 = 410, inside [0, 1600], so neither clamp fires.
  assert.equal(scrollTopToCenter({ ...view, bandTop: 700, bandHeight: 20 }), 410);
});

test("the container's own scroll is added back: the same band gives the same target", () => {
  // scrolled by 300, the same band sits 300px higher on screen: 400 - 100 + 300 = 600
  // into the content, so target 410 again. Leaving scrollTop out gives 110.
  assert.equal(
    scrollTopToCenter({ ...view, scrollTop: 300, bandTop: 400, bandHeight: 20 }),
    410,
  );
});

test("a band near the top clamps to 0 instead of a negative scroll", () => {
  // 150 - 100 + 0 = 50 into the content; centre 60; target 60 - 200 = -140 < 0, so the
  // lower clamp fires and the answer is 0.
  assert.equal(scrollTopToCenter({ ...view, bandTop: 150, bandHeight: 20 }), 0);
});

test("a band near the bottom clamps to the last scrollable position", () => {
  // 2000 - 100 + 0 = 1900 into the content; centre 1910; target 1910 - 200 = 1710 > 1600,
  // so the upper clamp fires and the answer is 2000 - 400 = 1600.
  assert.equal(scrollTopToCenter({ ...view, bandTop: 2000, bandHeight: 20 }), 1600);
});

test("content shorter than the viewport cannot scroll at all", () => {
  // scrollHeight 350 < clientHeight 400, so the range is [0, max(0, -50)] = [0, 0]. The
  // band is at 380 -> 380 - 100 = 280 into the content; centre 290; target 290 - 200 = 90,
  // which is > 0 and so passes the lower clamp, and is only brought to 0 by the
  // `Math.max(0, ...)` on the range. Without it the answer would be -50.
  assert.equal(
    scrollTopToCenter({ ...view, scrollHeight: 350, bandTop: 380, bandHeight: 20 }),
    0,
  );
});

test("a band taller than the viewport is still centred, not top-aligned", () => {
  // 700 -> 600 into the content; centre 600 + 800/2 = 1000; target 1000 - 200 = 800.
  assert.equal(scrollTopToCenter({ ...view, bandTop: 700, bandHeight: 800 }), 800);
});

test("a fractional position stays fractional", () => {
  // 700.5 - 100 + 0 = 600.5; centre 600.5 + 7/2 = 604; target 404.
  assert.equal(scrollTopToCenter({ ...view, bandTop: 700.5, bandHeight: 7 }), 404);
});

test("a container that has been laid out can be centred in", () => {
  assert.equal(canCenter({ ...view, bandTop: 700, bandHeight: 20 }), true);
});

test("a container with no height has nothing to centre in", () => {
  assert.equal(canCenter({ ...view, clientHeight: 0, bandTop: 700, bandHeight: 20 }), false);
  assert.equal(canCenter({ ...view, scrollHeight: 0, bandTop: 700, bandHeight: 20 }), false);
});

test("a band with no height, or a measurement that is not a number, is not centred on", () => {
  assert.equal(canCenter({ ...view, bandTop: 700, bandHeight: 0 }), false);
  assert.equal(canCenter({ ...view, bandTop: Number.NaN, bandHeight: 20 }), false);
  assert.equal(canCenter({ ...view, bandTop: 700, bandHeight: Infinity }), false);
});
