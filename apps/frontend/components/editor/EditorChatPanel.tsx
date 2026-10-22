"use client";

import { DragEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { Paperclip, Send, X } from "lucide-react";
import ChatMessageCard, { type FamilyCardActions } from "@/components/editor/ChatMessageCard";
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

export type ChatSuggestion = {
  key: string;
  label: string;
  prompt: string;
};

type Props = {
  messages: ChatMessage[];
  streamingText?: string | null;
  reasoningText?: string | null;
  thinkingLabel?: string;
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
  familyActions?: FamilyCardActions;
  suggestions?: ChatSuggestion[];
};

export default function EditorChatPanel({
  messages,
  streamingText,
  reasoningText,
  thinkingLabel,
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
  familyActions,
  suggestions,
}: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
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

  // The effect above disconnects its observer after 700ms, so a long stream
  // would stop following. This one is cheap enough to run on every flush.
  // It yields to a reader who has scrolled up: the threshold absorbs the
  // couple of lines a single 50ms flush can add without counting as scrolled.
  useEffect(() => {
    if (!streamingText) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    const fromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    if (fromBottom > 120) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [streamingText]);

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

  // Fills the composer and hands the caret over. Deliberately does not submit:
  // the user reads it, edits it, and sends it themselves.
  function pickSuggestion(prompt: string) {
    onDraftChange(prompt);
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    const end = prompt.length;
    textarea.setSelectionRange(end, end);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    acceptFile(event.dataTransfer.files[0]);
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
      <div className="min-h-0 min-w-0 flex-1 overflow-hidden px-4">
        <ScrollArea className="chat-scroll h-full min-w-0" viewportRef={viewportRef}>
          <div className="min-w-0 space-y-4 py-4">
            {messages.length === 0 && !sending ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">{emptyHint}</p>
                {suggestions && suggestions.length > 0 ? (
                  <ul className="space-y-1.5">
                    {suggestions.map((suggestion) => (
                      <li key={suggestion.key}>
                        <button
                          type="button"
                          data-testid={`chat-suggestion-${suggestion.key}`}
                          className="w-full rounded-md border border-border px-3 py-2 text-left text-sm transition-colors hover:border-ring hover:bg-muted/50"
                          onClick={() => pickSuggestion(suggestion.prompt)}
                        >
                          {suggestion.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            <AnimatePresence initial={false}>
              {messages.map((message, index) => (
                <motion.div
                  key={message.id ?? `${message.role}-${index}-${message.timestamp ?? ""}`}
                  className="min-w-0"
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
                    familyActions={familyActions}
                  />
                </motion.div>
              ))}
            </AnimatePresence>
            {reasoningText ? (
              <div className="min-w-0 rounded-md border border-dashed border-border/60 bg-muted/30 px-3 py-2">
                {thinkingLabel ? (
                  <p className="mb-1 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
                    {thinkingLabel}
                  </p>
                ) : null}
                <p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                  {reasoningText}
                </p>
              </div>
            ) : null}
            {streamingText ? (
              <div className="min-w-0">
                <ChatMessageCard
                  message={{ role: "assistant", content: streamingText }}
                  sending={sending}
                  currentSource={currentSource}
                  onRestoreEdit={onRestoreEdit}
                  familyActions={familyActions}
                />
              </div>
            ) : null}
            {sending && !streamingText && !reasoningText ? <LoadingMessage /> : null}
          </div>
        </ScrollArea>
      </div>
      <form
        className={cn(
          "space-y-2 border-t border-border px-3 py-2",
          dragging && "bg-hover",
        )}
        onSubmit={onSubmit}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {attachment ? (
          <div className="flex items-center gap-2 rounded-lg border border-border bg-sheet px-2 py-1.5 text-xs text-foreground">
            <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
            <button
              type="button"
              className="rounded p-0.5 hover:bg-accent"
              aria-label={attachRemoveLabel}
              onClick={() => onAttachmentChange(null)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
        {attachError ? <p className="text-xs text-destructive">{attachError}</p> : null}
        <div className="flex items-end justify-center space-x-1">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={sending}
            aria-label={attachLabel}
            title={`${attachLabel} · ${attachHint}`}
            className="h-12 w-12 shrink-0 text-muted-foreground hover:text-clay"
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
            ref={textareaRef}
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
            className="max-h-[120px] min-h-[48px] flex-1 resize-none px-2 text-sm transition-colors focus-visible:border-ring focus-visible:ring-ring/50"
            rows={1}
          />
          <Button
            type="submit"
            size="icon"
            disabled={!canSend()}
            aria-label={sendLabel}
            className="h-12 w-12 shrink-0 disabled:opacity-50"
          >
            <Send className="h-5 w-5" />
          </Button>
        </div>
      </form>
    </div>
  );
}
