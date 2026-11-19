import assert from "node:assert/strict";
import test from "node:test";

import { placePopover, type Rect } from "./popover-placement";

const viewport: Rect = { left: 0, top: 0, right: 1000, bottom: 800 };
const container: Rect = { left: 100, top: 50, right: 500, bottom: 700 };
const size = { width: 120, height: 40 };

function at(anchor: Rect, extra: Partial<Parameters<typeof placePopover>[0]> = {}) {
  return placePopover({ anchor, container, viewport, size, ...extra });
}

test("with room above, it sits above and centred on the selection", () => {
  const p = at({ left: 250, top: 300, right: 350, bottom: 320 });
  assert.deepEqual(p, { hidden: false, side: "above", left: 240, top: 252 });
});

test("with no room above, it flips below", () => {
  const p = at({ left: 250, top: 70, right: 350, bottom: 90 });
  assert.deepEqual(p, { hidden: false, side: "below", left: 240, top: 98 });
});

test("the room above is measured from the container, not the viewport", () => {
  // 100px below the viewport top, but only 30px below the panel's top edge.
  const p = placePopover({
    anchor: { left: 250, top: 130, right: 350, bottom: 150 },
    container: { left: 100, top: 100, right: 500, bottom: 700 },
    viewport,
    size,
  });
  assert.equal(p.hidden === false && p.side, "below");
});

test("it is clamped to the panel's width at both edges", () => {
  const left = at({ left: 100, top: 300, right: 110, bottom: 320 });
  assert.equal(left.hidden === false && left.left, 108);
  const right = at({ left: 490, top: 300, right: 500, bottom: 320 });
  assert.equal(right.hidden === false && right.left, 500 - 120 - 8);
});

test("a popover wider than the panel pins to the left margin instead of overflowing right", () => {
  const p = placePopover({
    anchor: { left: 150, top: 300, right: 200, bottom: 320 },
    container: { left: 100, top: 50, right: 200, bottom: 700 },
    viewport,
    size: { width: 300, height: 40 },
  });
  assert.equal(p.hidden === false && p.left, 108);
});

test("a selection scrolled out of the panel hides it, above or below", () => {
  assert.deepEqual(at({ left: 250, top: 10, right: 350, bottom: 40 }), { hidden: true });
  assert.deepEqual(at({ left: 250, top: 710, right: 350, bottom: 730 }), { hidden: true });
});

test("a selection half out of view is still beside the part that shows", () => {
  const p = at({ left: 250, top: 30, right: 350, bottom: 120 });
  assert.equal(p.hidden, false);
});

test("a phone prefers below, and still flips above when below has no room", () => {
  const mid = at({ left: 250, top: 300, right: 350, bottom: 320 }, { prefer: "below" });
  assert.equal(mid.hidden === false && mid.side, "below");
  const low = at({ left: 250, top: 660, right: 350, bottom: 680 }, { prefer: "below" });
  assert.equal(low.hidden === false && low.side, "above");
});

test("a tall selection that still leaves room below goes below, unclamped", () => {
  // bottom 640 + gap 8 + height 40 = 688 <= 700: it fits, so this is the ordinary path.
  const p = at({ left: 250, top: 60, right: 350, bottom: 640 });
  assert.deepEqual(p, { hidden: false, side: "below", left: 240, top: 648 });
});

test("a selection straddling the panel (no room on either side) takes the roomier side, clamped inside", () => {
  // above: 40 - 8 - 40 = -8 < 50 (no). below: 690 + 8 + 40 = 738 > 700 (no). Panel room
  // is 10px above the selection and 10px below: a tie goes below, clamped to the panel.
  const tie = at({ left: 250, top: 40, right: 350, bottom: 690 });
  assert.deepEqual(tie, { hidden: false, side: "below", left: 240, top: 660 });
  // 10px of panel above, none below: above, clamped down to the panel's top edge.
  const above = at({ left: 250, top: 60, right: 350, bottom: 710 });
  assert.deepEqual(above, { hidden: false, side: "above", left: 240, top: 50 });
  // None above, 10px below: below.
  const below = at({ left: 250, top: 30, right: 350, bottom: 690 });
  assert.deepEqual(below, { hidden: false, side: "below", left: 240, top: 660 });
});

test("a container clipped by the viewport uses the visible part", () => {
  const p = placePopover({
    anchor: { left: 350, top: 300, right: 390, bottom: 320 },
    container: { left: 100, top: 50, right: 1500, bottom: 700 },
    viewport: { left: 0, top: 0, right: 400, bottom: 800 },
    size,
  });
  assert.equal(p.hidden === false && p.left, 400 - 120 - 8);
});

test("a container with nothing visible hides it", () => {
  const p = placePopover({
    anchor: { left: 250, top: 300, right: 350, bottom: 320 },
    container: { left: 100, top: 900, right: 500, bottom: 1200 },
    viewport,
    size,
  });
  assert.deepEqual(p, { hidden: true });
});
