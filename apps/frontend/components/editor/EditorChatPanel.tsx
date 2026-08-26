"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { Send } from "lucide-react";
import ChatMessageCard from "@/components/editor/ChatMessageCard";
import LoadingMessage from "@/components/editor/LoadingMessage";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import type { ChatMessage } from "@/lib/api";

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
  onSubmit: (event: FormEvent) => void;
  sending: boolean;
  placeholder: string;
  sendLabel: string;
  emptyHint: string;
  ariaLabel: string;
};

export default function EditorChatPanel({
  messages,
  draft,
  onDraftChange,
  onSubmit,
  sending,
  placeholder,
  sendLabel,
  emptyHint,
  ariaLabel,
}: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const confirmEnterRef = useRef(false);

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

  function onCompositionStart() {
    composingRef.current = true;
  }

  function onCompositionEnd() {
    composingRef.current = false;
    // Zhuyin/IME often fires Enter after compositionend to confirm the candidate.
    confirmEnterRef.current = true;
    window.setTimeout(() => {
      confirmEnterRef.current = false;
    }, 50);
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
    if (draft.trim() && !sending) {
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-gray-900">
      <div className="min-h-0 flex-1 overflow-hidden px-4">
        <ScrollArea className="h-full" viewportRef={viewportRef}>
          <div className="space-y-4 py-4">
            {messages.length === 0 && !sending ? (
              <p className="text-sm text-gray-500 dark:text-gray-400">{emptyHint}</p>
            ) : null}
            <AnimatePresence mode="popLayout">
              {messages.map((message, index) => (
                <motion.div
                  key={message.id ?? `${message.role}-${index}-${message.timestamp ?? ""}`}
                  variants={messageVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  layout
                >
                  <ChatMessageCard message={message} />
                </motion.div>
              ))}
            </AnimatePresence>
            {sending ? <LoadingMessage /> : null}
          </div>
        </ScrollArea>
      </div>
      <form
        className="space-y-2 border-t border-gray-200 px-6 py-2 dark:border-gray-700"
        onSubmit={onSubmit}
      >
        <div className="flex items-center justify-center space-x-3">
          <Textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onCompositionStart={onCompositionStart}
            onCompositionEnd={onCompositionEnd}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            disabled={sending}
            aria-label={ariaLabel}
            className="max-h-[120px] min-h-[48px] flex-1 resize-none text-sm transition-colors focus:border-cyan-500 focus:ring-cyan-500"
            rows={1}
          />
          <Button
            type="submit"
            size="icon"
            disabled={!draft.trim() || sending}
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
