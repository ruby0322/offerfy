import { WALL_GLYPHS, WALL_WORDMARKS, type WallGlyph } from "@/components/landing/glyphs";

export type WallItem = {
  name: string;
  /** Simple Icons path data, or null when the name is set as type instead. */
  d: string | null;
  /** Brand colour, or null when the mark takes --ink so it survives both themes. */
  hex: string | null;
};

const GLYPHS: WallItem[] = WALL_GLYPHS.map((glyph: WallGlyph) => ({
  name: glyph.name,
  d: glyph.d,
  hex: glyph.hex,
}));

const WORDS: WallItem[] = WALL_WORDMARKS.map((name) => ({ name, d: null, hex: null }));

/**
 * The wall in display order: wordmarks spread evenly through the marks rather
 * than piled at the end, so no row of the carousel ends up all type. The rows
 * are contiguous slices of this list (see CompanyWall) — slicing cannot alias
 * the spacing the way round-robin dealing does.
 */
export const WALL_ITEMS: WallItem[] = (() => {
  const out: WallItem[] = [];
  const gap = Math.max(1, Math.round(GLYPHS.length / (WORDS.length + 1)));
  let next = 0;
  GLYPHS.forEach((glyph, index) => {
    out.push(glyph);
    if ((index + 1) % gap === 0 && next < WORDS.length) {
      out.push(WORDS[next]);
      next += 1;
    }
  });
  while (next < WORDS.length) {
    out.push(WORDS[next]);
    next += 1;
  }
  return out;
})();

/** Every employer named on the wall. companies.test.ts holds this to the board files. */
export const WALL_NAMES: string[] = WALL_ITEMS.map((item) => item.name);

/** Rows of the carousel, as contiguous slices. */
export function wallRows(count: number): WallItem[][] {
  const per = Math.ceil(WALL_ITEMS.length / count);
  return Array.from({ length: count }, (_, i) => WALL_ITEMS.slice(i * per, (i + 1) * per));
}
