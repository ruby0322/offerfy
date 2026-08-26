"use client";

import { cn } from "@/lib/utils";
import type { DiffLine } from "@/lib/edit-diff";

type Props = {
  lines: DiffLine[];
};

export default function EditDiff({ lines }: Props) {
  if (lines.length === 0) return null;
  return (
    <div className="chat-diff mt-2" role="region" aria-label="diff">
      {lines.map((line, index) => (
        <div
          key={`${line.op}-${index}`}
          className={cn("chat-diff-line", line.op === "add" ? "chat-diff-add" : "chat-diff-del")}
        >
          <span className="chat-diff-gutter" aria-hidden="true">
            {line.op === "add" ? "+" : "-"}
          </span>
          <span className="chat-diff-text">{line.text.length > 0 ? line.text : " "}</span>
        </div>
      ))}
    </div>
  );
}
