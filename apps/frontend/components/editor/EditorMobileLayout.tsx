"use client";

import { useCallback, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Sparkles, SlidersHorizontal, X } from "lucide-react";

// Fractions of the editor body the sheet may take. The floor leaves the tab
// strip and a line of content; the ceiling always keeps a strip of the resume
// in view, which is the whole point of the layout.
const MIN_HEIGHT = 0.32;
const MAX_HEIGHT = 0.88;
const DEFAULT_HEIGHT = 0.62;
const CLOSE_BELOW = 0.22;

type Props = {
  preview: ReactNode;
  panel: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Opens the sheet on chat with the composer focused. */
  onAskAi: () => void;
  labels: {
    ask: string;
    tools: string;
    close: string;
    expand: string;
    collapse: string;
    resize: string;
  };
};

/**
 * The phone layout: the resume is the page, and the editing tools live in a
 * sheet the reader pulls up over the lower part of it. Nothing is ever fully
 * hidden behind a tab, so an edit lands where it can be seen.
 */
export default function EditorMobileLayout({
  preview,
  panel,
  open,
  onOpenChange,
  onAskAi,
  labels,
}: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const dragRef = useRef<{ startY: number; startHeight: number; bodyHeight: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  const clamp = (value: number) => Math.min(MAX_HEIGHT, Math.max(CLOSE_BELOW / 2, value));

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const bodyHeight = bodyRef.current?.clientHeight ?? 0;
      if (!bodyHeight) return;
      dragRef.current = { startY: event.clientY, startHeight: height, bodyHeight };
      setDragging(true);
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [height],
  );

  const onPointerMove = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const delta = (drag.startY - event.clientY) / drag.bodyHeight;
    setHeight(clamp(drag.startHeight + delta));
  }, []);

  const onPointerUp = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      setDragging(false);
      event.currentTarget.releasePointerCapture(event.pointerId);
      // Dragged most of the way down: read that as "put it away", and leave a
      // sensible height behind for the next time it opens.
      setHeight((current) => {
        if (current < CLOSE_BELOW) {
          onOpenChange(false);
          return DEFAULT_HEIGHT;
        }
        return Math.max(MIN_HEIGHT, current);
      });
    },
    [onOpenChange],
  );

  function onGrabberKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHeight((current) => Math.min(MAX_HEIGHT, current + 0.1));
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setHeight((current) => Math.max(MIN_HEIGHT, current - 0.1));
    } else if (event.key === "Escape") {
      onOpenChange(false);
    }
  }

  const expanded = height >= MAX_HEIGHT - 0.01;

  return (
    <div ref={bodyRef} className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1">{preview}</div>
      {open ? (
        <section
          className="flex min-h-0 flex-col border-t border-border bg-background"
          style={{
            height: `${height * 100}%`,
            transition: dragging ? undefined : "height 0.18s ease",
          }}
        >
          <div className="relative flex shrink-0 items-center justify-center px-1">
            <div
              role="separator"
              aria-orientation="horizontal"
              aria-label={labels.resize}
              tabIndex={0}
              className="flex h-11 w-full cursor-row-resize touch-none items-center justify-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onKeyDown={onGrabberKeyDown}
            >
              <span className="h-1 w-10 rounded-full bg-border" aria-hidden="true" />
            </div>
            <div className="absolute right-1 flex items-center gap-0.5">
              <button
                type="button"
                className="chat-composer-tool"
                aria-label={expanded ? labels.collapse : labels.expand}
                onClick={() => setHeight(expanded ? DEFAULT_HEIGHT : MAX_HEIGHT)}
              >
                {expanded ? (
                  <ChevronDown className="size-[1.15rem]" aria-hidden="true" />
                ) : (
                  <ChevronUp className="size-[1.15rem]" aria-hidden="true" />
                )}
              </button>
              <button
                type="button"
                className="chat-composer-tool"
                aria-label={labels.close}
                onClick={() => onOpenChange(false)}
              >
                <X className="size-[1.15rem]" aria-hidden="true" />
              </button>
            </div>
          </div>
          <div className="min-h-0 min-w-0 flex-1">{panel}</div>
        </section>
      ) : (
        <div className="flex shrink-0 items-center gap-2 border-t border-border bg-background px-3 py-2">
          <button
            type="button"
            data-testid="editor-mobile-ask"
            className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-lg border border-rule bg-sheet px-3 text-left text-sm text-muted-foreground"
            onClick={onAskAi}
          >
            <Sparkles className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <span className="truncate">{labels.ask}</span>
          </button>
          <button
            type="button"
            data-testid="editor-mobile-tools"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg border border-rule bg-sheet text-muted-foreground"
            aria-label={labels.tools}
            onClick={() => onOpenChange(true)}
          >
            <SlidersHorizontal className="size-[1.15rem]" aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
