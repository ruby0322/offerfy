"use client";

import { ReactNode, useState } from "react";
import ZoomToolbar from "@/components/editor/ZoomToolbar";
import PreviewTextLayer from "@/components/editor/PreviewTextLayer";
import AtsStrip from "@/components/editor/AtsStrip";
import { Button } from "@/components/ui/button";
import type { AtsCheckName } from "@/lib/ats-checks";
import type { AtsReport, PreviewTextLayer as TextLayer } from "@/lib/api";

type Props = {
  previewUrls: string[];
  /** Parallel to `previewUrls` where it is known; short or empty otherwise. */
  textLayers?: TextLayer[];
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

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-background">
      <div className="relative min-h-0 flex-1">
        <div className="h-full min-h-0 overflow-auto">
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
