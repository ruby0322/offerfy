"use client";

import { DragEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { ArrowDown, Paperclip, Send, X } from "lucide-react";
import ChatMessageCard, { type FamilyCardActions } from "@/components/editor/ChatMessageCard";
import LoadingMessage from "@/components/editor/LoadingMessage";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import UsageLimitCard from "@/components/editor/UsageLimitCard";
import UsageMeter from "@/components/UsageMeter";
import type { ChatMessage, Usage, UsageRefusal } from "@/lib/api";
import {
  commandTerm,
  filterCommands,
  moveCommandIndex,
  type ChatCommand,
} from "@/lib/chat-commands";

// How far from the bottom counts as "the reader has scrolled away". Bigger
// than the 120px the streaming follow uses, so a jump button does not blink in
// and out while text flushes.
const SCROLLED_UP_PX = 160;

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

export type { ChatCommand };

type Props = {
  messages: ChatMessage[];
  /** Draws the "older messages are not sent" marker above this message id. */
  contextCutMessageId?: string | null;
  contextCutTitle?: string;
  contextCutDetail?: string;
  contextCutJumpLabel?: string;
  scrollToLatestLabel?: string;
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
  /** The `/` menu. Absent or empty leaves the composer plain. The copy for
   *  each command comes from the caller, as every other label here does. */
  commands?: ChatCommand[];
  commandsTitle?: string;
  /** Marks a command whose prompt stops mid-sentence and waits for the user. */
  commandsNeedsInput?: string;
  /** One line under the openers telling a first-time reader that `/` exists. */
  commandsHint?: string;
  /** Bumped by the caller to put the caret in the composer, e.g. when a phone
   *  raises the tool sheet straight into chat. */
  focusKey?: number;
  /** This week's AI allowance, and the refusal when it has run out. Grouped
   *  rather than spread over nine props: the shell owns the copy, as it does
   *  for every other label here. */
  usage?: UsagePanelProps;
};

export type UsagePanelProps = {
  usage: Usage | null;
  refusal: UsageRefusal | null;
  onDismissRefusal: () => void;
  labels: {
    meter: string;
    editsLeft: string | null;
    reset: string;
    limitTitle: string;
    offer: string | null;
    limitReset: string;
    signIn: string | null;
    settings: string | null;
    learnMore: string;
    dismiss: string;
  };
};

export default function EditorChatPanel({
  messages,
  contextCutMessageId,
  contextCutTitle,
  contextCutDetail,
  contextCutJumpLabel,
  scrollToLatestLabel,
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
  commands,
  commandsTitle,
  commandsNeedsInput,
  commandsHint,
  focusKey,
  usage,
}: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // The notice at the end of the transcript scrolls this into view. Only one
  // message can match the cut, so a single ref is enough.
  const cutMarkerRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const commandListRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const confirmEnterRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [scrolledUp, setScrolledUp] = useState(false);
  // Where the keyboard is in the `/` menu, tagged with the term it was moved
  // in. Carrying the term means a new term resets the selection while
  // rendering, instead of in an effect that would render twice to fix it.
  const [commandCursor, setCommandCursor] = useState({ term: "", index: 0 });
  // The term Escape shut the menu on. Without it the menu springs straight
  // back up on the next keypress, which is not what Escape means.
  const [dismissedTerm, setDismissedTerm] = useState<string | null>(null);

  // The `/` menu. A null term is ordinary prose and the shut state; the list
  // is short enough that filtering it on every keystroke costs nothing.
  const commandList = commands ?? [];
  const commandSearch = commandList.length > 0 && !sending ? commandTerm(draft) : null;
  const commandMatches =
    commandSearch === null || commandSearch === dismissedTerm
      ? []
      : filterCommands(commandList, commandSearch);
  const commandsOpen = commandMatches.length > 0;
  const commandIndex = commandCursor.term === commandSearch ? commandCursor.index : 0;
  // Clamped rather than trusted: the list shrinks as the term gets longer.
  const activeCommand = commandsOpen
    ? commandMatches[Math.min(commandIndex, commandMatches.length - 1)]
    : null;

  // Keeps the keyboard selection in view when the list outgrows its box.
  useEffect(() => {
    commandListRef.current
      ?.querySelector<HTMLElement>('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeCommand?.key]);

  // Whether to offer the jump back down. Watches the content box as well as
  // the viewport: a turn that appends a long answer moves the bottom away from
  // a reader who has not touched the scrollbar, and no scroll event fires.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const update = () => {
      const fromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      setScrolledUp(fromBottom > SCROLLED_UP_PX);
    };
    update();
    viewport.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(viewport);
    const inner = viewport.firstElementChild;
    if (inner) observer.observe(inner);
    return () => {
      viewport.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);

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

  // The caller raises this when it has just put the chat in front of the
  // reader and the next thing they want is the keyboard.
  useEffect(() => {
    if (!focusKey) return;
    textareaRef.current?.focus();
  }, [focusKey]);

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
    // While the `/` menu is up it owns the arrows, Tab, Escape and Enter --
    // Enter above all, or picking a command would send it in the same stroke.
    if (commandsOpen) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        selectCommand(moveCommandIndex(commandIndex, commandMatches.length, delta));
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setDismissedTerm(commandSearch);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        // An IME candidate list is driven by Enter too. It gets there first.
        if (composingRef.current || event.nativeEvent.isComposing) return;
        event.preventDefault();
        if (activeCommand) pickCommand(activeCommand);
        return;
      }
    }
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
  // the user reads it, edits it, and sends it themselves. Both the openers and
  // the `/` menu end here, so a command behaves like a draft you wrote.
  function fillComposer(prompt: string) {
    onDraftChange(prompt);
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    const end = prompt.length;
    textarea.setSelectionRange(end, end);
  }

  function pickCommand(command: ChatCommand) {
    setDismissedTerm(null);
    fillComposer(command.prompt);
  }

  function selectCommand(index: number) {
    if (commandSearch === null) return;
    setCommandCursor({ term: commandSearch, index });
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    acceptFile(event.dataTransfer.files[0]);
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
      <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden px-4">
        <ScrollArea className="chat-scroll h-full min-w-0" viewportRef={viewportRef}>
          <div className="min-w-0 space-y-4 pt-4 pb-10">
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
                          onClick={() => fillComposer(suggestion.prompt)}
                        >
                          {suggestion.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {commandsHint && commandList.length > 0 ? (
                  <p className="text-xs text-muted-foreground">{commandsHint}</p>
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
                  {/* Sits where the model's view of the chat begins. Above it
                      the messages are still here, still yours -- they are just
                      not in the request any more. */}
                  {contextCutMessageId && message.id === contextCutMessageId ? (
                    <div
                      ref={cutMarkerRef}
                      className="mb-3 border-t border-dashed border-border/70 pt-3"
                    >
                      <p className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
                        {contextCutTitle}
                      </p>
                      {contextCutDetail ? (
                        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                          {contextCutDetail}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
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
            {/* Last line of the transcript, because that is where the reader
                is after every turn -- the divider that explains this sits
                where the trim happens, which can be fifty messages up. */}
            {contextCutMessageId ? (
              <p className="flex flex-wrap items-baseline gap-x-1.5 pt-1 text-[0.7rem] leading-snug text-muted-foreground">
                <span>{contextCutTitle}</span>
                {contextCutJumpLabel ? (
                  <button
                    type="button"
                    className="underline underline-offset-2 hover:text-foreground"
                    onClick={() =>
                      cutMarkerRef.current?.scrollIntoView({
                        behavior: "smooth",
                        block: "center",
                      })
                    }
                  >
                    {contextCutJumpLabel}
                  </button>
                ) : null}
              </p>
            ) : null}
          </div>
        </ScrollArea>
        {scrolledUp ? (
          <button
            type="button"
            aria-label={scrollToLatestLabel}
            title={scrollToLatestLabel}
            onClick={() => {
              const viewport = viewportRef.current;
              viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
            }}
            className="absolute bottom-3 right-6 z-10 flex size-8 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-md transition hover:text-foreground"
          >
            <ArrowDown className="size-4" aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {usage?.refusal ? (
        <UsageLimitCard
          refusal={usage.refusal}
          title={usage.labels.limitTitle}
          offer={usage.labels.offer}
          resetLabel={usage.labels.limitReset}
          signInLabel={usage.labels.signIn}
          settingsLabel={usage.labels.settings}
          learnMoreLabel={usage.labels.learnMore}
          onDismiss={usage.onDismissRefusal}
          dismissLabel={usage.labels.dismiss}
        />
      ) : null}
      <form
        className="chat-composer"
        onSubmit={onSubmit}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {usage ? (
          <UsageMeter
            usage={usage.usage}
            label={usage.labels.meter}
            editsLeftLabel={usage.labels.editsLeft}
            resetLabel={usage.labels.reset}
          />
        ) : null}
        {commandsOpen ? (
          <div className="chat-command-menu">
            {commandsTitle ? (
              <p className="chat-command-menu-title">{commandsTitle}</p>
            ) : null}
            <div
              ref={commandListRef}
              id="chat-command-menu"
              role="listbox"
              aria-label={commandsTitle ?? ariaLabel}
              className="chat-command-list"
            >
              {commandMatches.map((command, index) => {
                const selected = command.key === activeCommand?.key;
                return (
                  <button
                    key={command.key}
                    type="button"
                    id={`chat-command-${command.key}`}
                    role="option"
                    aria-selected={selected}
                    data-selected={selected ? "true" : undefined}
                    data-testid={`chat-command-${command.key}`}
                    className="chat-command-item"
                    // Mousedown would blur the composer before the click lands
                    // and take the caret with it. The click still fires.
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseMove={() => selectCommand(index)}
                    onClick={() => pickCommand(command)}
                  >
                    <span className="chat-command-slug">/{command.key}</span>
                    <span className="chat-command-text">
                      <span className="chat-command-label">{command.label}</span>
                      <span className="chat-command-description">{command.description}</span>
                    </span>
                    {command.needsInput && commandsNeedsInput ? (
                      <span className="chat-command-badge">{commandsNeedsInput}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
        <div className="chat-composer-field" data-dragging={dragging ? "true" : undefined}>
          {attachment ? (
            <div className="chat-composer-chip">
              <Paperclip className="size-3.5 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
              <button
                type="button"
                className="chat-composer-chip-remove"
                aria-label={attachRemoveLabel}
                onClick={() => onAttachmentChange(null)}
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          ) : null}
          {attachError ? <p className="chat-composer-error">{attachError}</p> : null}
          <Textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => {
              const value = event.target.value;
              // An Escape applies to the term it was pressed on. Typing out of
              // command mode altogether retires it, so coming back to the same
              // term later opens the menu again.
              if (dismissedTerm !== null && commandTerm(value) === null) {
                setDismissedTerm(null);
              }
              onDraftChange(value);
            }}
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
            aria-autocomplete="list"
            aria-expanded={commandsOpen}
            aria-controls={commandsOpen ? "chat-command-menu" : undefined}
            aria-activedescendant={
              activeCommand ? `chat-command-${activeCommand.key}` : undefined
            }
            className="max-h-[140px] min-h-[2.5rem] resize-none border-0 bg-transparent px-2 py-2 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 md:text-sm"
            rows={1}
          />
          <div className="chat-composer-row">
            <button
              type="button"
              className="chat-composer-tool"
              disabled={sending}
              aria-label={attachLabel}
              title={`${attachLabel} · ${attachHint}`}
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip className="size-[1.15rem]" aria-hidden="true" />
            </button>
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
            <button
              type="submit"
              className="chat-composer-send"
              disabled={!canSend()}
              aria-label={sendLabel}
              title={sendLabel}
            >
              <Send className="size-[1.15rem]" aria-hidden="true" />
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
