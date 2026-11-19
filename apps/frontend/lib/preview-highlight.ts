/**
 * Which rendered lines to mark on the preview, and where they sit.
 *
 * Pure, like the rest of the fields tab's logic: the overlay has no component test
 * runner, so everything that can be a function of numbers and a projection is one.
 *
 * The bands are the backend's best guess at which lines a unit occupies, and they say
 * only `{page, y, height}`. This file draws what they say: it does not widen, filter
 * or repair them, and every input it cannot make sense of becomes "draw nothing".
 */

import type { PreviewBand } from "./api";
import type { Projection } from "./structured-fields";
import {
  blockKey,
  blockUnitIds,
  findBlock,
  sectionKey,
} from "./structural-controls";

/** A band as CSS lengths, each a share of the page width (`cqw`). */
export type BandRect = { top: string; height: string };

/**
 * How many `cqw` one unit of the text layer's coordinates is: 1cqw is a hundredth of the
 * page box, and the box is as wide as the layer. The one copy of the formula -- the text
 * layer and the bands both call it, so they cannot drift apart.
 */
export function cqwUnit(layerWidth: number): number {
  return 100 / layerWidth;
}

/**
 * One band as `cqw` lengths: the same formula `PreviewTextLayer` uses, so the two agree
 * line for line. 1cqw is a hundredth of the page box, and the box is as wide as
 * `pageWidth`, so a length of `v` in the layer's units is `v * 100 / pageWidth`.
 *
 * Null for anything that cannot be drawn honestly: a page with no usable width (no text
 * layer, or a zero-sized one), or a band that is not a positive-height finite number.
 */
export function bandRect(band: PreviewBand, pageWidth: number): BandRect | null {
  if (!Number.isFinite(pageWidth) || pageWidth <= 0) return null;
  if (!Number.isFinite(band.y) || !Number.isFinite(band.height) || band.height <= 0) {
    return null;
  }
  const unit = cqwUnit(pageWidth);
  return { top: `${band.y * unit}cqw`, height: `${band.height * unit}cqw` };
}

/** The bands that sit on one page; a page the preview does not have gets none. */
export function bandsOnPage(bands: PreviewBand[], page: number): PreviewBand[] {
  return bands.filter((band) => band.page === page);
}

/**
 * The bands of these units, in the order the ids are given. A unit the map does not have
 * -- or an empty map, which is what the backend sends when it could not say -- adds
 * nothing. `hasOwn`, because a unit id is data and `bands["constructor"]` is a function.
 */
export function bandsForUnits(
  bands: Record<string, PreviewBand[]>,
  ids: readonly string[],
): PreviewBand[] {
  const out: PreviewBand[] = [];
  for (const id of new Set(ids)) {
    if (!Object.hasOwn(bands, id)) continue;
    const rows = bands[id];
    if (Array.isArray(rows)) out.push(...rows);
  }
  return out;
}

/** What an armed delete is aimed at: `StructuredPanel`'s `armed`, as far as this needs. */
export type ArmedTarget = { target: string; key: string };

/**
 * Every unit id the armed delete would take, or `[]` when nothing is armed *or the arm no
 * longer means what it did*.
 *
 * Ids are positional: after a reorder `b3` can name a different entry. The arm therefore
 * carries the key it was made against, and it only counts while that key still matches --
 * the same test `ArmedDelete` is drawn with. A target is a block (or a bullet of one) or
 * a whole section, so both are tried; a section takes its title and every block in it.
 *
 * Recomputed from the projection on every call and never stored: the highlight must
 * follow the arm, and a copy would outlive it.
 */
export function armedUnitIds(
  projection: Projection | null,
  armed: ArmedTarget | null,
): string[] {
  if (!projection || !armed) return [];
  const found = findBlock(projection, armed.target);
  if (found && blockKey(projection, armed.target) === armed.key) {
    return blockUnitIds(found.block);
  }
  const section = projection.sections.find((x) => x.id === armed.target);
  if (section && sectionKey(projection, section.id) === armed.key) {
    return [
      ...(section.titleUnit ? [section.titleUnit] : []),
      ...section.blocks.flatMap(blockUnitIds),
    ];
  }
  return [];
}

/**
 * Whether the fields tab should fetch highlight bands for `source`.
 *
 * Keyed on provenance: `describes` is the source the bands on screen came from (null when
 * unknown), not whether there are any. Empty bands are a real answer for a document with
 * no editable line, and a non-empty set can belong to the document before this one -- an
 * `/edit` answer without pages leaves the old ones behind. `askedFor` is the source
 * already fetched for, so an empty answer is not asked again on every render.
 */
export function bandsNeedFetch(state: {
  describes: string | null;
  askedFor: string | null;
  source: string;
}): boolean {
  return state.describes !== state.source && state.askedFor !== state.source;
}

/**
 * What the preview's scroll container measures, in the DOM's own pixels. The `cqw`
 * lengths are already resolved by the browser, so nothing here knows a page width.
 */
export type ScrollMeasure = {
  /** The band's `getBoundingClientRect().top`. */
  bandTop: number;
  /** The band's `getBoundingClientRect().height`. */
  bandHeight: number;
  /** The container's `getBoundingClientRect().top`. */
  containerTop: number;
  /** The container's current `scrollTop`. */
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
};

/**
 * Whether the container has been laid out enough to say where to scroll. A container
 * with no height (not yet laid out, or display:none) or a band with none yields no
 * scroll: nothing beats a scroll to the wrong place.
 */
export function canCenter(m: ScrollMeasure): boolean {
  return (
    Object.values(m).every(Number.isFinite) &&
    m.clientHeight > 0 &&
    m.scrollHeight > 0 &&
    m.bandHeight > 0
  );
}

/**
 * The `scrollTop` that puts the band's centre in the middle of the container, clamped to
 * what the container can actually scroll to -- at the top or bottom of the document the
 * band cannot be centred, and the container stops rather than showing blank gutter.
 *
 * The band's place in *content* coordinates is its distance below the container's top
 * edge plus how far the container is already scrolled. Always a number: whether to
 * scroll at all is `canCenter`'s question, not this one's.
 */
export function scrollTopToCenter(m: ScrollMeasure): number {
  const contentTop = m.bandTop - m.containerTop + m.scrollTop;
  const target = contentTop + m.bandHeight / 2 - m.clientHeight / 2;
  const max = Math.max(0, m.scrollHeight - m.clientHeight);
  return Math.min(Math.max(target, 0), max);
}
