"use client";

import { useMemo } from "react";
import type { PreviewBand } from "@/lib/api";
import { bandRect } from "@/lib/preview-highlight";

// The rows of the rendered page that a field in the fields tab owns. A sibling of
// `PreviewTextLayer` inside the same page box, in the same coordinates and the same
// `cqw` lengths, so it follows the zoom control and the panel resize with no measuring.
//
// It is decoration that repeats what the form already says, so it is `aria-hidden`; and
// it never takes a click, because the text layer beside it exists so the preview can be
// selected. Both of those, and the colours, live on `.preview-highlight` in
// `globals.css`: an unlayered rule there beats a utility on the same element.

type Props = {
  /** Bands on this page only. */
  bands: PreviewBand[];
  kind: "edit" | "delete";
  /** The text layer's width: the unit the bands are measured in. */
  pageWidth: number;
};

export default function PreviewHighlight({ bands, kind, pageWidth }: Props) {
  const rects = useMemo(
    () =>
      bands
        .map((band) => bandRect(band, pageWidth))
        .filter((rect) => rect !== null),
    [bands, pageWidth],
  );

  if (rects.length === 0) return null;

  return (
    <div className="preview-highlight" data-kind={kind} aria-hidden="true">
      {rects.map((rect, index) => (
        <div
          key={index}
          className="preview-highlight-band"
          style={{ top: rect.top, height: rect.height }}
        />
      ))}
    </div>
  );
}
