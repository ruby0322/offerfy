"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
import ZoomToolbar from "@/components/editor/ZoomToolbar";
import PreviewTextLayer from "@/components/editor/PreviewTextLayer";
import PreviewHighlight from "@/components/editor/PreviewHighlight";
import AtsStrip from "@/components/editor/AtsStrip";
import { Button } from "@/components/ui/button";
import type { AtsCheckName } from "@/lib/ats-checks";
import type { AtsReport, PreviewBand, PreviewTextLayer as TextLayer } from "@/lib/api";
import { bandsOnPage, canCenter, scrollTopToCenter } from "@/lib/preview-highlight";

/**
 * Scroll `container` so the first band of `kind` is in the middle of it. Only the
 * container is moved: `scrollIntoView` would walk every scrollable ancestor, including
 * the document, and in the side-by-side layout that drags the field out from under the
 * cursor that just clicked it. Returns whether it scrolled; false means there was no band
 * to go to, or the container was not laid out, and nothing moved.
 *
 * The bands are in DOM order -- page by page, top to bottom -- so the first one found is
 * the first line of a wrapped field. The browser has already resolved their `cqw`
 * lengths, so the geometry is read off the elements rather than recomputed.
 */
function centreFirstBand(container: HTMLElement, kind: "edit" | "delete"): boolean {
  const band = container.querySelector(
    `.preview-highlight[data-kind="${kind}"] .preview-highlight-band`,
  );
  if (!band) return false;
  const bandRect = band.getBoundingClientRect();
  const measure = {
    bandTop: bandRect.top,
    bandHeight: bandRect.height,
    containerTop: container.getBoundingClientRect().top,
    scrollTop: container.scrollTop,
    clientHeight: container.clientHeight,
    scrollHeight: container.scrollHeight,
  };
  if (!canCenter(measure)) return false;
  const top = scrollTopToCenter(measure);
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  container.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
  return true;
}

type Props = {
  previewUrls: string[];
  /** Parallel to `previewUrls` where it is known; short or empty otherwise. */
  textLayers?: TextLayer[];
  /** The lines of the focused field, across every page; drawn in the accent. */
  editBands?: PreviewBand[];
  /** The lines an armed delete would take, across every page; drawn as danger. */
  deleteBands?: PreviewBand[];
  /**
   * What the bands are of: the focused field, and the armed delete. Arriving at a new
   * non-null key scrolls its first band to the middle once; the bands refetching under
   * the same key (every commit does) must not move the page back under the reader, and
   * null (blurred, disarmed) does nothing.
   */
  editKey?: string | null;
  deleteKey?: string | null;
  previewAlt: string;
  previewError: string;
  report: AtsReport | null;
  downloadLabel: string;
  downloading: boolean;
  onDownload: () => void;
  onFixAts?: (name: AtsCheckName) => void;
  fixingAts?: boolean;
  overlay?: ReactNode;
  showAts?: boolean;
};

export default function EditorPreview({
  previewUrls,
  textLayers,
  editBands,
  deleteBands,
  editKey,
  deleteKey,
  previewAlt,
  previewError,
  report,
  downloadLabel,
  downloading,
  onDownload,
  onFixAts,
  fixingAts,
  overlay,
  showAts = true,
}: Props) {
  const [scale, setScale] = useState(1);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  /** The key each kind last scrolled to, so one arrival scrolls once. */
  const centred = useRef<{ edit: string | null; delete: string | null }>({
    edit: null,
    delete: null,
  });

  useEffect(() => {
    const container = scrollerRef.current;
    const wants = [
      ["edit", editKey ?? null],
      ["delete", deleteKey ?? null],
    ] as const;
    for (const [kind, key] of wants) {
      if (key === null) {
        centred.current[kind] = null;
        continue;
      }
      if (centred.current[kind] === key || !container) continue;
      // No band yet (the fetch is still out, or this unit has none) leaves the key
      // unmarked, so the bands arriving later still get their one scroll.
      if (centreFirstBand(container, kind)) centred.current[kind] = key;
    }
  }, [editKey, deleteKey, editBands, deleteBands, textLayers, previewUrls]);

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-background">
      <div className="relative min-h-0 flex-1">
        <div ref={scrollerRef} className="h-full min-h-0 overflow-auto">
          <div className="sticky top-0 z-30 flex justify-end pointer-events-none">
            <div className="m-3 flex items-center gap-2 pointer-events-auto">
              <Button
                type="button"
                size="sm"
                className="bg-primary text-primary-foreground hover:opacity-90"
                onClick={onDownload}
                disabled={downloading}
              >
                {downloadLabel}
              </Button>
              <ZoomToolbar value={scale} onChange={setScale} />
            </div>
          </div>
          <div className="flex justify-center px-4 pt-2 pb-4">
            {previewUrls.length > 0 ? (
              <div
                className="w-full max-w-[52rem] origin-top"
                style={{ transform: `scale(${scale})` }}
              >
                <div className="flex flex-col gap-4">
                  {previewUrls.map((url, index) => (
                    // The page box is the text layer's container: its own width
                    // is what the overlay's cqw lengths resolve against, so it
                    // has to be exactly the image's width.
                    <div
                      key={url}
                      className="relative w-full overflow-hidden rounded-lg border border-rule bg-sheet"
                      style={{ containerType: "inline-size" }}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={url}
                        alt={`${previewAlt} ${index + 1}`}
                        className="block h-auto w-full select-none"
                        draggable={false}
                      />
                      {/* Under the text layer, not over it: both ignore the pointer, but
                          the layer that makes the page selectable stays on top. Bands
                          are measured in the text layer's units, so a page without one
                          has nothing to draw them against. */}
                      {textLayers?.[index] ? (
                        <>
                          <PreviewHighlight
                            kind="edit"
                            bands={bandsOnPage(editBands ?? [], index)}
                            pageWidth={textLayers[index].width}
                          />
                          <PreviewHighlight
                            kind="delete"
                            bands={bandsOnPage(deleteBands ?? [], index)}
                            pageWidth={textLayers[index].width}
                          />
                        </>
                      ) : null}
                      {textLayers?.[index] ? (
                        <PreviewTextLayer layer={textLayers[index]} />
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{previewError}</p>
            )}
          </div>
        </div>
        {overlay}
      </div>
      {showAts ? (
        <AtsStrip report={report} onFix={onFixAts} fixing={fixingAts} />
      ) : null}
    </div>
  );
}
