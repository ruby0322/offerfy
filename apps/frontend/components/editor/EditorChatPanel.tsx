"use client";

import { DragEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { Paperclip, Send, X } from "lucide-react";
import ChatMessageCard from "@/components/editor/ChatMessageCard";
import LoadingMessage from "@/components/editor/LoadingMessage";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import type { ChatMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED_EXT = new Set(["pdf", "png", "jpg", "jpeg", "webp", "txt", "md"]);
const ACCEPT =
  ".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,application/pdf,image/png,image/jpeg,image/webp,text/plain,text/markdown";

const messageVariants: Variants = {
  hidden: {
    opacity: 0,
    y: 20,
    scale: 0.95,
  },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      duration: 0.3,
      ease: "easeOut",
    },
  },
  exit: {
    opacity: 0,
    y: -10,
    transition: {
      duration: 0.2,
    },
  },
};

type Props = {
  messages: ChatMessage[];
  draft: string;
  onDraftChange: (value: string) => void;
  attachment: File | null;
  onAttachmentChange: (file: File | null) => void;
  onSubmit: (event: FormEvent) => void;
  sending: boolean;
  placeholder: string;
  sendLabel: string;
  emptyHint: string;
  ariaLabel: string;
  attachLabel: string;
  attachRemoveLabel: string;
  attachHint: string;
  attachBadType: string;
  attachTooLarge: string;
  currentSource?: string;
  onRestoreEdit?: (source: string) => void;
};

export default function EditorChatPanel({
  messages,
  draft,
  onDraftChange,
  attachment,
  onAttachmentChange,
  onSubmit,
  sending,
  placeholder,
  sendLabel,
  emptyHint,
  ariaLabel,
  attachLabel,
  attachRemoveLabel,
  attachHint,
  attachBadType,
  attachTooLarge,
  currentSource,
  onRestoreEdit,
}: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  const confirmEnterRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const scrollToBottom = () => {
      viewport.scrollTop = viewport.scrollHeight;
    };
    scrollToBottom();
    const inner = viewport.firstElementChild;
    const observer = inner ? new ResizeObserver(scrollToBottom) : null;
    if (inner) observer?.observe(inner);
    const stop = window.setTimeout(() => observer?.disconnect(), 700);
    return () => {
      window.clearTimeout(stop);
      observer?.disconnect();
    };
  }, [messages, sending]);

  function acceptFile(file: File | undefined) {
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!ALLOWED_EXT.has(ext)) {
      setAttachError(attachBadType);
      return;
    }
    if (file.size > MAX_BYTES) {
      setAttachError(attachTooLarge);
      return;
    }
    setAttachError(null);
    onAttachmentChange(file);
  }

  function onCompositionStart() {
    composingRef.current = true;
  }

  function onCompositionEnd() {
    composingRef.current = false;
    confirmEnterRef.current = true;
    window.setTimeout(() => {
      confirmEnterRef.current = false;
    }, 50);
  }

  function canSend() {
    return Boolean(draft.trim() || attachment) && !sending;
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    const ime =
      composingRef.current ||
      confirmEnterRef.current ||
      event.nativeEvent.isComposing ||
      event.keyCode === 229 ||
      event.nativeEvent.keyCode === 229;
    if (ime) return;
    event.preventDefault();
    if (canSend()) {
      event.currentTarget.form?.requestSubmit();
    }
  }

  function onDragOver(event: DragEvent) {
    event.preventDefault();
    setDragging(true);
  }

  function onDragLeave() {
    setDragging(false);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    acceptFile(event.dataTransfer.files[0]);
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-gray-900">
      <div className="min-h-0 flex-1 overflow-hidden px-4">
        <ScrollArea className="h-full" viewportRef={viewportRef}>
          <div className="space-y-4 py-4">
            {messages.length === 0 && !sending ? (
              <p className="text-sm text-gray-500 dark:text-gray-400">{emptyHint}</p>
            ) : null}
            <AnimatePresence initial={false}>
              {messages.map((message, index) => (
                <motion.div
                  key={message.id ?? `${message.role}-${index}-${message.timestamp ?? ""}`}
                  variants={messageVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                >
                  <ChatMessageCard
                    message={message}
                    sending={sending}
                    currentSource={currentSource}
                    onRestoreEdit={onRestoreEdit}
                  />
                </motion.div>
              ))}
            </AnimatePresence>
            {sending ? <LoadingMessage /> : null}
          </div>
        </ScrollArea>
      </div>
      <form
        className={cn(
          "space-y-2 border-t border-gray-200 px-3 py-2 dark:border-gray-700",
          dragging && "bg-cyan-50 dark:bg-cyan-950/40",
        )}
        onSubmit={onSubmit}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {attachment ? (
          <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200">
            <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
            <button
              type="button"
              className="rounded p-0.5 hover:bg-gray-200 dark:hover:bg-gray-700"
              aria-label={attachRemoveLabel}
              onClick={() => onAttachmentChange(null)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
        {attachError ? <p className="text-xs text-red-600 dark:text-red-400">{attachError}</p> : null}
        <div className="flex items-end justify-center space-x-1">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={sending}
            aria-label={attachLabel}
            title={`${attachLabel} · ${attachHint}`}
            className="h-12 w-12 shrink-0 text-gray-600 hover:text-cyan-700 dark:text-gray-300 dark:hover:text-cyan-300"
            onClick={() => fileRef.current?.click()}
          >
            <Paperclip className="h-5 w-5" />
          </Button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept={ACCEPT}
            disabled={sending}
            onChange={(event) => {
              acceptFile(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <Textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onCompositionStart={onCompositionStart}
            onCompositionEnd={onCompositionEnd}
            onKeyDown={onKeyDown}
            onPaste={(event) => {
              const file = event.clipboardData.files[0];
              if (file) {
                event.preventDefault();
                acceptFile(file);
              }
            }}
            placeholder={placeholder}
            disabled={sending}
            aria-label={ariaLabel}
            className="max-h-[120px] min-h-[48px] flex-1 resize-none px-2 text-sm transition-colors focus:border-cyan-500 focus:ring-cyan-500"
            rows={1}
          />
          <Button
            type="submit"
            size="icon"
            disabled={!canSend()}
            aria-label={sendLabel}
            className="h-12 w-12 shrink-0 bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50"
          >
            <Send className="h-5 w-5" />
          </Button>
        </div>
      </form>
    </div>
  );
}
