"use client";

import { Fragment, useMemo } from "react";
import type { PreviewTextLayer as Layer } from "@/lib/api";

// Typst's SVG draws glyph outlines and carries no text, so the preview is a
// picture. This lays the page's text back over that picture, transparent, one
// absolutely positioned line at a time -- the same trick a PDF viewer uses to
// make a rendered page selectable.

// The hidden lines are measured in this family before being stretched to fit,
// so the spans have to be rendered in it too or the stretch is wrong.
const FONT_FAMILY = "sans-serif";

// `undefined` means "not tried yet"; `null` means there is no canvas to
// measure with (the server, or a browser that refuses the 2d context).
let measureContext: CanvasRenderingContext2D | null | undefined;

/** Width of `text` at `size`, in the units the layer is laid out in. */
function naturalWidth(text: string, size: number): number {
  if (measureContext === undefined) {
    measureContext =
      typeof document === "undefined"
        ? null
        : document.createElement("canvas").getContext("2d");
  }
  if (!measureContext) return 0;
  measureContext.font = `${size}px ${FONT_FAMILY}`;
  return measureContext.measureText(text).width;
}

type Props = {
  layer: Layer;
};

export default function PreviewTextLayer({ layer }: Props) {
  const lines = useMemo(() => {
    if (layer.width <= 0 || layer.height <= 0) return [];
    // Every length is a share of the page width, so the layer tracks the
    // picture through the zoom control and the panel resize with no measuring:
    // 1cqw is a hundredth of the page box the two of them share.
    const unit = 100 / layer.width;
    return layer.spans.map((span, index) => {
      const natural = naturalWidth(span.text, span.size);
      return {
        key: `${index}:${span.x},${span.y}`,
        text: span.text,
        left: `${span.x * unit}cqw`,
        top: `${span.y * unit}cqw`,
        fontSize: `${span.size * unit}cqw`,
        // Pull the hidden line onto the glyphs it stands for, so the highlight
        // marks the words rather than a box beside them.
        transform: natural > 0 ? `scaleX(${span.width / natural})` : undefined,
      };
    });
  }, [layer]);

  if (lines.length === 0) return null;

  return (
    <div
      className="preview-text-layer absolute inset-0 cursor-text"
      style={{ fontFamily: FONT_FAMILY }}
    >
      {lines.map((line) => (
        <Fragment key={line.key}>
          <span
            className="absolute whitespace-pre text-transparent"
            style={{
              left: line.left,
              top: line.top,
              fontSize: line.fontSize,
              lineHeight: 1,
              transform: line.transform,
              transformOrigin: "left top",
            }}
          >
            {line.text}
          </span>
          {/* Every line is taken out of the flow, and a selection across two of
              them would otherwise be copied as one run-on string. These breaks
              are what put the line endings back; they draw nothing. */}
          <br role="presentation" />
        </Fragment>
      ))}
    </div>
  );
}
