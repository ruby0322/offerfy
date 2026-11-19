/**
 * Where the format popover goes, as a pure function of rectangles.
 *
 * Every rectangle is in viewport coordinates (what `getBoundingClientRect` returns), and
 * the answer is for `position: fixed`. `container` is the panel's scroll container: the
 * popover is kept inside its width, and hidden once the selection has scrolled out of its
 * height, so it never floats over the tab bar or the preview beside it.
 */
export type Rect = { left: number; top: number; right: number; bottom: number };

export type Placement =
  | { hidden: true }
  | { hidden: false; left: number; top: number; side: "above" | "below" };

export type PlacementInput = {
  /** The selection (or the link the caret is in). */
  anchor: Rect;
  container: Rect;
  viewport: Rect;
  size: { width: number; height: number };
  /** Space between the selection and the popover. */
  gap?: number;
  /** Space kept between the popover and the visible edges. */
  margin?: number;
  /** A phone draws its own menu above a selection, so it asks for below first. */
  prefer?: "above" | "below";
};

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), Math.max(lo, hi));

export function placePopover(input: PlacementInput): Placement {
  const { anchor, container, viewport, size } = input;
  const gap = input.gap ?? 8;
  const margin = input.margin ?? 8;
  const prefer = input.prefer ?? "above";

  const vis: Rect = {
    left: Math.max(container.left, viewport.left),
    top: Math.max(container.top, viewport.top),
    right: Math.min(container.right, viewport.right),
    bottom: Math.min(container.bottom, viewport.bottom),
  };
  if (vis.right <= vis.left || vis.bottom <= vis.top) return { hidden: true };
  // Scrolled wholly out of view: nothing to be beside.
  if (anchor.bottom <= vis.top || anchor.top >= vis.bottom) return { hidden: true };

  const aboveTop = anchor.top - gap - size.height;
  const belowTop = anchor.bottom + gap;
  const fitsAbove = aboveTop >= vis.top;
  const fitsBelow = belowTop + size.height <= vis.bottom;

  let side: "above" | "below";
  if (fitsAbove && fitsBelow) side = prefer;
  else if (fitsAbove) side = "above";
  else if (fitsBelow) side = "below";
  // Neither fits (a tall selection in a short panel): take the side with more room, and
  // the clamp below keeps the popover on screen rather than off the end of it.
  else side = anchor.top - vis.top >= vis.bottom - anchor.bottom ? "above" : "below";

  const rawTop = side === "above" ? aboveTop : belowTop;
  const top = clamp(rawTop, vis.top, vis.bottom - size.height);
  const centre = (anchor.left + anchor.right) / 2;
  const left = clamp(centre - size.width / 2, vis.left + margin, vis.right - size.width - margin);
  return { hidden: false, left, top, side };
}
