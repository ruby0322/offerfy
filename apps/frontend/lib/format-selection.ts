/**
 * Which text a selection covers, and which formatting buttons that earns.
 *
 * Pure, over structural types, so it is tested without a DOM (the same device as
 * `MarkNode` in `rich-marks.ts`): a live `Node` and `Range` satisfy these shapes with no
 * cast, which the type-level proofs in `format-selection.test.ts` keep honest under
 * `tsc --noEmit`.
 *
 * This is the function whose bug costs the person text -- the link wrapper cuts the
 * document where it says -- so it works from document order alone: every boundary point
 * becomes "before character N of the Kth text node", and a text node is covered between
 * the two.
 */
export type SelNode<N> = {
  nodeType: number;
  textContent: string | null;
  childNodes: ArrayLike<N>;
  getAttribute?: (name: string) => string | null;
};

/** The part of a `Range` read here. A live `Range` is assignable. */
export type SelRange<N> = {
  startContainer: N;
  startOffset: number;
  endContainer: N;
  endOffset: number;
};

/** A run of one text node, in UTF-16 units: what `Range` offsets and `splitText` use. */
export type Segment<N> = { node: N; start: number; end: number };

const ELEMENT_NODE = 1;
const TEXT_NODE = 3;

/**
 * The text nodes the range covers, minus opaque chips. A chip's text is its raw source,
 * which `domToMarks` reads from `data-raw`; cutting or wrapping inside it would leave
 * two halves that both claim the whole. `touchesChip` says the selection covered some of
 * one, so a caller can refuse what it cannot do to a chip.
 *
 * A text node the range merely touches (an end at its offset 0, a start at its end) is
 * not covered.
 */
export function segmentsOf<N extends SelNode<N>>(
  root: N,
  range: SelRange<N>,
): { segments: Segment<N>[]; touchesChip: boolean } {
  const texts: N[] = [];
  const inChip: boolean[] = [];
  const before = new Map<N, number>(); // text nodes that precede entering the node
  const after = new Map<N, number>(); // ...and after leaving it

  const walk = (node: N, chip: boolean): void => {
    before.set(node, texts.length);
    if (node.nodeType === TEXT_NODE) {
      texts.push(node);
      inChip.push(chip);
    } else if (node.nodeType === ELEMENT_NODE) {
      const inside = chip || (node.getAttribute?.("data-raw") ?? null) !== null;
      for (let i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i], inside);
    }
    after.set(node, texts.length);
  };
  walk(root, false);

  const point = (container: N, offset: number): { ti: number; off: number } | null => {
    if (container.nodeType === TEXT_NODE) {
      const ti = before.get(container);
      return ti === undefined ? null : { ti, off: offset };
    }
    const kids = container.childNodes;
    if (offset < kids.length) {
      const ti = before.get(kids[offset]);
      return ti === undefined ? null : { ti, off: 0 };
    }
    const ti = after.get(container);
    return ti === undefined ? null : { ti, off: 0 };
  };

  const s = point(range.startContainer, range.startOffset);
  const e = point(range.endContainer, range.endOffset);
  const segments: Segment<N>[] = [];
  let touchesChip = false;
  if (!s || !e) return { segments, touchesChip };

  for (let i = Math.max(s.ti, 0); i <= e.ti && i < texts.length; i++) {
    const len = texts[i].textContent?.length ?? 0;
    const start = i === s.ti ? s.off : 0;
    const end = Math.min(i === e.ti ? e.off : len, len);
    if (end <= start) continue;
    if (inChip[i]) touchesChip = true;
    else segments.push({ node: texts[i], start, end });
  }
  return { segments, touchesChip };
}

export type ButtonPlan = {
  /** `chip`: shown but disabled, because a chip is in the selection. `hidden`: a bare caret. */
  format: "enabled" | "hidden" | "chip";
  link: "new" | "edit";
  remove: boolean;
};

/**
 * Bold and italic go through `execCommand`, whose treatment of a non-editable island in
 * the selection differs by browser and cannot be told from here: it may wrap the chip's
 * raw source in `<b>`, which would write `*$x$*` into the person's Typst. So a selection
 * that covers a chip cannot be bolded at all.
 */
export function buttonsFor(s: {
  collapsed: boolean;
  editing: boolean;
  linkCount: number;
  touchesChip: boolean;
}): ButtonPlan {
  return {
    format: s.collapsed ? "hidden" : s.touchesChip ? "chip" : "enabled",
    link: s.editing ? "edit" : "new",
    remove: s.linkCount > 0,
  };
}
