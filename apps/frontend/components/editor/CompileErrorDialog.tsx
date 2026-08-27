"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

type Props = {
  error: string;
  copyLabel: string;
  copiedLabel: string;
  askLabel: string;
  asking?: boolean;
  onCopy: () => Promise<void> | void;
  onAsk: () => void;
};

export default function CompileErrorDialog({
  error,
  copyLabel,
  copiedLabel,
  askLabel,
  asking = false,
  onCopy,
  onAsk,
}: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await onCopy();
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div
      role="alert"
      data-slot="compile-error-dialog"
      className="pointer-events-auto absolute inset-x-3 bottom-3 z-20 flex max-h-[45%] flex-col overflow-hidden rounded-lg border border-destructive/30 bg-sheet"
    >
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-xs leading-relaxed text-destructive">
        {error}
      </pre>
      <div className="flex shrink-0 justify-end gap-2 border-t border-destructive/20 px-3 py-2">
        <Button type="button" size="sm" variant="outline" onClick={() => void handleCopy()}>
          {copied ? copiedLabel : copyLabel}
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={asking}
          onClick={onAsk}
        >
          {askLabel}
        </Button>
      </div>
    </div>
  );
}
