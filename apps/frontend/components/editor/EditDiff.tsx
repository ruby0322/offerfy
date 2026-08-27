"use client";

import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DiffLine } from "@/lib/edit-diff";

type Props = {
  lines: DiffLine[];
  action?: ReactNode;
};

export default function EditDiff({ lines, action }: Props) {
  const t = useTranslations("editor");
  const [open, setOpen] = useState(false);
  if (lines.length === 0) return null;
  const added = lines.filter((line) => line.op === "add").length;
  const removed = lines.filter((line) => line.op === "del").length;
  return (
    <div className="chat-diff-wrap mt-1" data-open={open ? "true" : "false"} role="region" aria-label="diff">
      <div className="chat-diff-bar">
        <button
          type="button"
          data-testid="tool-edit-diff-toggle"
          aria-expanded={open}
          aria-label={open ? t("toolEditDiffCollapse") : t("toolEditDiffExpand")}
          className="tool-edit-action inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronDown
            className={cn("size-3 transition-transform", !open && "-rotate-90")}
            aria-hidden="true"
          />
          <span className="flex items-center gap-1.5 tabular-nums" data-testid="tool-edit-diff-stats">
            {added > 0 ? (
              <span className="text-success">+{added}</span>
            ) : null}
            {removed > 0 ? (
              <span className="text-destructive">-{removed}</span>
            ) : null}
          </span>
        </button>
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {open ? (
        <div className="chat-diff">
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
      ) : null}
    </div>
  );
}
