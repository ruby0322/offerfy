"use client";

import { useState } from "react";
import ZoomToolbar from "@/components/editor/ZoomToolbar";
import AtsStrip from "@/components/editor/AtsStrip";
import { Button } from "@/components/ui/button";
import type { AtsReport } from "@/lib/api";

type Props = {
  previewUrls: string[];
  previewAlt: string;
  previewError: string;
  report: AtsReport | null;
  downloadLabel: string;
  downloading: boolean;
  onDownload: () => void;
};

export default function EditorPreview({
  previewUrls,
  previewAlt,
  previewError,
  report,
  downloadLabel,
  downloading,
  onDownload,
}: Props) {
  const [scale, setScale] = useState(1);

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-gray-50 dark:bg-gray-950">
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="sticky top-0 z-30 flex justify-end pointer-events-none">
          <div className="m-3 flex items-center gap-2 pointer-events-auto">
            <Button
              type="button"
              size="sm"
              className="bg-cyan-600 hover:bg-cyan-700"
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
            <div className="origin-top" style={{ transform: `scale(${scale})` }}>
              <div className="flex flex-col gap-4">
                {previewUrls.map((url, index) => (
                  <div
                    key={url}
                    className="overflow-hidden rounded-lg bg-white shadow-md dark:bg-gray-900"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={url}
                      alt={`${previewAlt} ${index + 1}`}
                      className="block h-auto w-[min(100%,52rem)]"
                    />
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">{previewError}</p>
          )}
        </div>
      </div>
      <AtsStrip report={report} />
    </div>
  );
}
