"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";

/**
 * Cards you swipe through, one role to a page.
 *
 * The mechanism only. What is on a card belongs to whoever builds the cards:
 * a weekly pick carries a score and the model's sentence, a posting off the
 * morning's ingest carries a location and no score at all, and this file must
 * not know the difference.
 *
 * CSS scroll-snap, not a gesture handler. The browser already owns the hard
 * parts -- inertia, rubber-banding, the trackpad, a horizontal wheel, the
 * scrollbar a desktop reader expects -- and it owns them before any of our
 * JavaScript has run. The script here only reads the position back out to
 * draw the dots, so a card is swipeable even while the page is still
 * hydrating.
 *
 * WHICH CARD IS "THIS CARD" is measured, not derived. The obvious version,
 * `round(scrollLeft / clientWidth)`, assumes a card is exactly as wide as the
 * viewport; these are inset so the next one peeks past the edge (which is how
 * a reader learns the gesture without being told), so the step is neither the
 * scroller's width nor a number this file could keep in step with the CSS.
 * Comparing each card's own midpoint against the middle of the viewport holds
 * for any width, gap or inset, and keeps holding when the fold changes them.
 *
 * The END CARD is not a role and is not counted. It is the terminus: swiping
 * past the last pick should land somewhere that says so and offers the next
 * move, rather than refusing to move.
 */

export type DeckCard = { key: string; node: ReactNode };

type Props = {
  cards: DeckCard[];
  /** The terminus, after the last card. Not counted in the position. */
  end?: ReactNode;
  /** Names the carousel for a screen reader; the section's own heading. */
  label: string;
  testId?: string;
};

export default function RoleDeck({ cards, end, label, testId }: Props) {
  const t = useTranslations("dashboard");
  // Arrows are rendered, not hidden: a button a thumb cannot reach is still
  // in the tab order and still read out. False on the first paint, which is
  // the wide layout -- and a deck only ever renders once its data has
  // arrived, well after the query is readable.
  const narrow = useMediaQuery(NARROW_SCREEN);
  const scroller = useRef<HTMLUListElement>(null);
  const [active, setActive] = useState(0);
  const total = cards.length;
  const stops = total + (end ? 1 : 0);

  const sync = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const mid = el.scrollLeft + el.clientWidth / 2;
    let best = 0;
    let bestGap = Number.POSITIVE_INFINITY;
    for (let i = 0; i < el.children.length; i += 1) {
      const node = el.children[i] as HTMLElement;
      // offsetLeft is relative to the offsetParent, which is the scroller
      // itself -- it carries `relative` for exactly this.
      const gap = Math.abs(node.offsetLeft + node.offsetWidth / 2 - mid);
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    }
    setActive(best);
  }, []);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    // One read per frame at most: a flick fires scroll events faster than
    // React can re-render, and every one of them measures every card.
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        sync();
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    sync();
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
    // stops, so a deck that gains or loses cards re-measures rather than
    // keeping an index that no longer exists.
  }, [sync, stops]);

  const go = useCallback(
    (index: number) => {
      const el = scroller.current;
      if (!el) return;
      const clamped = Math.max(0, Math.min(stops - 1, index));
      const node = el.children[clamped] as HTMLElement | undefined;
      if (!node) return;
      // scrollTo rather than scrollIntoView: the latter also scrolls every
      // ancestor, which on a phone means the page jumps to put the deck in
      // the middle of the screen when all that was asked for was the next
      // card.
      el.scrollTo({
        left: node.offsetLeft - (el.clientWidth - node.offsetWidth) / 2,
        behavior: "smooth",
      });
    },
    [stops],
  );

  if (stops === 0) return null;

  const position = Math.min(active + 1, total);

  return (
    // Capped and centred on a wide screen: a card wide enough to fill a
    // 1024px column would run the model's sentence to a 140-character line,
    // and the same card left-aligned leaves half the page visibly empty on
    // one side only, which reads as a layout that broke rather than one card
    // at a time.
    <div
      className={narrow ? "mt-3" : "mx-auto mt-3 flex max-w-[32rem] items-center gap-3"}
      data-testid={testId}
    >
      {narrow ? null : (
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-rule bg-sheet text-muted-foreground disabled:opacity-40"
          onClick={() => go(active - 1)}
          disabled={active === 0}
          aria-label={t("deckPrev")}
        >
          ‹
        </button>
      )}

      <div className="min-w-0 flex-1">
        <ul
          ref={scroller}
          tabIndex={0}
          role="group"
          aria-roledescription="carousel"
          aria-label={label}
          onKeyDown={(event) => {
            // The scroller moves under the arrow keys on its own, but only
            // when it holds the focus; this also works from a button inside
            // a card, where the keys otherwise do nothing.
            if (event.key === "ArrowRight") go(active + 1);
            else if (event.key === "ArrowLeft") go(active - 1);
            else return;
            event.preventDefault();
          }}
          /* narrow: the deck bleeds to both screen edges and puts the page's
             own gutter back as scroll padding, so the neighbouring card shows
             past the edge instead of being clipped by the gutter. */
          className="relative flex snap-x snap-mandatory gap-2 overflow-x-auto overscroll-x-contain pb-1 narrow:-mx-4 narrow:px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {cards.map((card, index) => (
            <li
              key={card.key}
              className="flex shrink-0 basis-full snap-center"
              role="group"
              aria-roledescription="slide"
              aria-label={t("deckPosition", { index: index + 1, total })}
            >
              {card.node}
            </li>
          ))}
          {end ? (
            <li className="flex shrink-0 basis-full snap-center">{end}</li>
          ) : null}
        </ul>

        {/* One dot per ROLE. The end card has none: a dot for it would make
            the row say there is one more role than the count beside it. On
            the end card the last dot stays lit, which is also where swiping
            back one card takes you. */}
        <div className="mt-2.5 flex items-center justify-center gap-1.5">
          {Array.from({ length: total }, (_, index) => (
            <button
              key={index}
              type="button"
              onClick={() => go(index)}
              aria-label={t("deckPosition", { index: index + 1, total })}
              aria-current={index === Math.min(active, total - 1)}
              className={
                index === Math.min(active, total - 1)
                  ? "h-1.5 w-4 rounded-full bg-clay"
                  : "size-1.5 rounded-full bg-rule"
              }
            />
          ))}
          <span className="ml-1.5 text-[0.7rem] text-muted-foreground [font-variant-numeric:tabular-nums]">
            {t("deckPosition", { index: position, total })}
          </span>
        </div>
      </div>

      {narrow ? null : (
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-rule bg-sheet text-muted-foreground disabled:opacity-40"
          onClick={() => go(active + 1)}
          disabled={active >= stops - 1}
          aria-label={t("deckNext")}
        >
          ›
        </button>
      )}
    </div>
  );
}
