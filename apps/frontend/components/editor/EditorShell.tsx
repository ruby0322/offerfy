"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  AlignLeft,
  ArrowLeft,
  History,
  LayoutTemplate,
  MessageCircle,
  Settings,
  SlidersHorizontal,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import AccountMenu from "@/components/AccountMenu";
import ConfirmDeleteDialog from "@/components/ConfirmDeleteDialog";
import { useToast } from "@/components/Toaster";
import CompileErrorDialog from "@/components/editor/CompileErrorDialog";
import CreateTailorDialog, { type TailorDraft } from "@/components/editor/CreateTailorDialog";
import EditorChatPanel, {
  type ChatCommand,
  type ChatSuggestion,
} from "@/components/editor/EditorChatPanel";
import EditorHeader from "@/components/editor/EditorHeader";
import EditorHistoryPanel from "@/components/editor/EditorHistoryPanel";
import EditorMobileLayout from "@/components/editor/EditorMobileLayout";
import EditorPreview from "@/components/editor/EditorPreview";
import EditorSettingsPanel from "@/components/editor/EditorSettingsPanel";
import EditorTemplatePanel, {
  type AppliedTemplateNote,
} from "@/components/editor/EditorTemplatePanel";
import MemoryPanel from "@/components/editor/MemoryPanel";
import ResumeFamilyMenu from "@/components/editor/ResumeFamilyMenu";
import StructuredPanel from "@/components/editor/StructuredPanel";
import type { FamilyCardActions } from "@/components/editor/ChatMessageCard";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Link, useRouter } from "@/i18n/navigation";
import { NARROW_SCREEN, useMediaQuery, useVisualViewportHeight } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import {
  ApiError,
  acceptMemory,
  claimResumes,
  compileResume,
  createTailor,
  deleteMemory,
  deleteResume,
  dismissMemory,
  exportPdf,
  fetchModels,
  fetchUsage,
  getAtsReport,
  getChatMessages,
  type ContextCut,
  getImport,
  getMe,
  getPreviewPages,
  getResume,
  getResumeFamily,
  listMemory,
  getResumeShare,
  postEdit,
  publishResume,
  putResumeSource,
  putSelectedModel,
  sendChat,
  startImport,
  startTailorRun,
  streamImport,
  streamTailorRun,
  getTailorRun,
  switchTemplate,
  type AtsReport,
  type ChatMessage,
  type EditResult,
  type ImportStatus,
  type IntentBody,
  type MemoryItem,
  type PreviewBand,
  type PreviewPages,
  type PreviewTextLayer,
  type Resume,
  type ResumeFamily,
  type ResumeKind,
  type ResumeSource,
  type ShareState,
  typstCompileDetail,
  usageRefusalOf,
} from "@/lib/api";
import type { AuthUser, ModelRow, ModelTable, Usage, UsageRefusal } from "@/lib/api";
import { formatUserAttachmentMessage, parseToolPayload } from "@/lib/chat-tools";
import type { AtsCheckName } from "@/lib/ats-checks";
import { createEditQueue } from "@/lib/edit-queue";
import { loadEditorTab, normalizeEditorTab, saveEditorTab } from "@/lib/editor-tab";
import { mayPutSource, shellShouldPutSource, sourceOwnerFor } from "@/lib/field-ownership";
import { bandsForUnits } from "@/lib/preview-highlight";
import type { Projection } from "@/lib/structured-fields";
import {
  emptyImportCursor,
  importBlocksSave,
  importCardIndex,
  importTurnStartsAt,
  nextImportCursor,
  planImportAdopt,
  seedImportCursor,
  toImportView,
  type ImportSnapshot,
} from "@/lib/import-progress";
import type { TailorAction } from "@/lib/tailor-actions";
import type { TailorCardActions } from "@/components/editor/TailorSuggestions";
import {
  emptyTailorCursor,
  nextTailorCursor,
  planTailorAdopt,
  seedTailorCursor,
  shouldStartTailorRun,
  tailorBlocksSave,
  tailorRunBlocksComposer,
  toTailorRunView,
  type TailorRunSnapshot,
} from "@/lib/tailor-run";
import { peekPendingCreate, takePendingCreate } from "@/lib/pending-create";

const TypstSourceEditor = dynamic(
  () => import("@/components/editor/TypstSourceEditor"),
  { ssr: false },
);

function newMessageId(): string {
  const webCrypto = globalThis.crypto as Crypto | undefined;
  if (typeof webCrypto?.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 11)}`;
}

function withMessageId(message: ChatMessage): ChatMessage {
  return message.id ? message : { ...message, id: newMessageId() };
}

/** `resumes.import_status` as a snapshot implies it. The settings panel and
 *  the three admin pages read that column; this is what keeps the value they
 *  show from being whatever it was at mount for the rest of the session. */
function importStatusOf(snap: ImportSnapshot): ImportStatus {
  return snap.status === "running" ? "pending" : snap.status;
}

const composerDraftKey = (id: string) => `offerfy:composer:${id}`;

function loadComposerDraft(id: string): string {
  try {
    return sessionStorage.getItem(composerDraftKey(id)) ?? "";
  } catch {
    return "";
  }
}

function saveComposerDraft(id: string, value: string) {
  try {
    if (value) sessionStorage.setItem(composerDraftKey(id), value);
    else sessionStorage.removeItem(composerDraftKey(id));
  } catch {
    /* storage can be unavailable in private mode */
  }
}

/** How long a whole-document write waits for a field commit to land before refusing. */
const EDIT_DRAIN_TIMEOUT_MS = 15_000;
const EDIT_DRAIN_POLL_MS = 50;
/**
 * How long one `/edit` may take before the request is abandoned. Its own worst case is
 * about 90s (three 30s typst subprocess timeouts), so this is that plus room; what it
 * is really for is the request that never answers at all, which would otherwise hold
 * the source -- and so the autosave -- for the life of the page.
 */
const EDIT_TIMEOUT_MS = 120_000;

const editorTabClassName =
  "editor-tab h-auto flex-none rounded-none border-0 bg-transparent shadow-none data-[state=active]:bg-transparent data-[state=active]:shadow-none";

type Props = {
  resumeId: string;
};

export default function EditorShell({ resumeId }: Props) {
  const t = useTranslations("editor");
  const tAts = useTranslations("ats");
  const tCommon = useTranslations("common");
  const tNav = useTranslations("nav");
  const locale = useLocale();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ResumeKind | undefined>();
  const [createdAt, setCreatedAt] = useState<string | undefined>();
  const [resumeSource, setResumeSource] = useState<ResumeSource | undefined>();
  const [resumeLocale, setResumeLocale] = useState<string | undefined>();
  const [importStatus, setImportStatus] = useState<ImportStatus | undefined>();
  const [source, setSource] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [textLayers, setTextLayers] = useState<PreviewTextLayer[]>([]);
  /** Line-unit id -> its rendered lines; `{}` when the server could not say. */
  const [bands, setBands] = useState<Record<string, PreviewBand[]>>({});
  /** The line unit the fields tab has focus in, and what its armed delete would take. */
  const [focusedUnit, setFocusedUnit] = useState<string | null>(null);
  const [armedUnits, setArmedUnits] = useState<string[]>([]);
  const editBands = useMemo(
    () => (focusedUnit ? bandsForUnits(bands, [focusedUnit]) : []),
    [bands, focusedUnit],
  );
  const deleteBands = useMemo(() => bandsForUnits(bands, armedUnits), [bands, armedUnits]);
  const [ats, setAts] = useState<AtsReport | null>(null);
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Which earlier messages the model is no longer sent. Only ever moves
  // forward -- the cut is monotonic -- so this never needs clearing.
  const [contextCut, setContextCut] = useState<ContextCut | null>(null);
  // Text that has streamed in but not yet been finalized into a real message.
  // Deliberately outside `messages`: it carries no id and must never persist.
  const [streamingText, setStreamingText] = useState<string | null>(null);
  // The model's summary of its own thinking. Shown while the turn runs, never
  // persisted: it is not something the assistant said to the user.
  const [reasoningText, setReasoningText] = useState<string | null>(null);
  // Whether the running turn has produced anything but thinking yet -- a tool
  // call or a word of the answer. The chat panel needs it to tell "still
  // waiting" apart from "working, and not narrating it", because most turns
  // on most models carry no reasoning summary at all.
  const [turnProgressed, setTurnProgressed] = useState(false);
  const [draft, setDraft] = useState(() => loadComposerDraft(resumeId));
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  // Covers the `switchTemplate` round trip itself, which runs before
  // `sendUserChat` (or `onRestoreHistory`) ever flips `sending` -- without
  // this the Apply button stays clickable for that whole await and a second
  // click starts a second switch, whose own chat turn then silently no-ops
  // against `sendUserChat`'s own `sendingRef` guard. Same shape as
  // `importActionPending` guarding `onImportAction`.
  const [templateApplyPending, setTemplateApplyPending] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [chatUnavailable, setChatUnavailable] = useState(false);
  // This week's AI allowance. Fetched once on mount, then kept current by the
  // `quota` frame every turn ends with -- no polling.
  const [usage, setUsage] = useState<Usage | null>(null);
  const [refusal, setRefusal] = useState<UsageRefusal | null>(null);
  // The model catalogue and this person's pick of it. Server-owned: the
  // selection lives on their account (or their guest session), so there is
  // nothing to remember in the browser and nothing for a stale localStorage
  // value to disagree with. Null means the fetch failed, which costs the
  // picker and not the turn.
  const [models, setModels] = useState<ModelTable | null>(null);
  const [leftTab, setLeftTab] = useState(() =>
    peekPendingCreate(resumeId) ? "chat" : normalizeEditorTab(loadEditorTab(resumeId)),
  );
  // The preview keeps its own pane in both layouts and is never a tab, and a tab name
  // this build does not have (a rollback, an older session) would select nothing at
  // all, so what sessionStorage returns is normalised before it reaches the strip.
  const activeTab = normalizeEditorTab(leftTab);
  // The bytes the server is known to hold: the answer to the last PUT, or the source
  // an `/edit` or a restore returned. Only ever set from such an answer, so the worst
  // it can be is out of date in the direction that saves anyway.
  const [savedSource, setSavedSource] = useState<string | null>(null);
  /** Adopt source the server already holds: the buffer and that record move together. */
  const adoptServerSource = useCallback((next: string) => {
    setSavedSource(next);
    setSource(next);
  }, []);
  // Whether the fields tab is on screen holding a projection, and how many of its
  // edits are still in flight. Together they say who owns `source`; see
  // `sourceOwnerFor`.
  //
  // `fieldsOpen` is derived, never stored, and the panel mounts on the same condition:
  // the fields tab is up **and** the server is known to hold what this mount holds.
  // That is the handover in one line -- the panel cannot be on screen reading a
  // projection of a document older than this buffer -- and it means no code path can
  // leave the flag up after the tab is gone, which would silence the autosave for the
  // rest of the session.
  const fieldsOpen = activeTab === "fields" && savedSource === source;
  const [editsInFlight, setEditsInFlight] = useState(0);
  // Mirrored for the save paths that are not effects: `flushSave` and the pre-turn PUT
  // have to read ownership at call time, because a commit starts inside the blur that
  // a click on another tab fires -- one event before the click handler that leaves the
  // editor. `editsInFlightRef` is written before `postEdit`'s first await, so it is
  // already up when a handler later in that same event reads it.
  const fieldsOpenRef = useRef(false);
  const editsInFlightRef = useRef(0);
  const sourceOwner = sourceOwnerFor({ fieldsOpen, editsInFlight });
  const currentOwner = useCallback(
    () =>
      sourceOwnerFor({
        fieldsOpen: fieldsOpenRef.current,
        editsInFlight: editsInFlightRef.current,
      }),
    [],
  );
  useEffect(() => {
    // One commit behind `fieldsOpen` at worst, and only ever on the way up -- where
    // the two differ exactly when `savedSource === source`, so there is nothing a save
    // path could overwrite anyway.
    fieldsOpenRef.current = fieldsOpen;
  }, [fieldsOpen]);
  // The buffer and the server's copy, for the paths that read them outside a render.
  // A commit's answer lands seconds after it was sent -- `/edit` compiles -- and by
  // then its closure's `source` is whatever it was when the person typed.
  const sourceRef = useRef("");
  const savedRef = useRef<string | null>(null);
  useEffect(() => {
    sourceRef.current = source;
    savedRef.current = savedSource;
  }, [savedSource, source]);
  // A phone cannot hold the source and the preview side by side. There the
  // resume is the page and the tools ride in a sheet pulled up over it.
  const narrow = useMediaQuery(NARROW_SCREEN);
  // dvh does not shrink for the on-screen keyboard, so without this the
  // composer the reader is typing into sits under it.
  const visibleHeight = useVisualViewportHeight(narrow);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [chatFocusKey, setChatFocusKey] = useState(0);
  const [compileError, setCompileError] = useState<string | null>(null);
  // The auth check already runs for `claimResumes` below; keeping the
  // account it returns, rather than just a boolean, is what lets the header
  // draw the avatar without asking a second time.
  const [me, setMe] = useState<AuthUser | null | undefined>(undefined);
  const signedIn = !!me;
  // Just the destination, after the arrow that already says "back". These are
  // the nav's own names for the two places rather than editor copies of them,
  // so the word in the header cannot drift from the word in the menu.
  //
  // Still unknown reads as the signed-in label: that is the common case in
  // the editor, so its width is the right one to hold, and the header keeps
  // it invisible until it is actually true.
  const backLabel = signedIn || me === undefined ? tNav("dashboard") : tNav("brand");
  const [unpublishedChanges, setUnpublishedChanges] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");
  const [publishing, setPublishing] = useState(false);
  // Whether this resume has a public link, which is what the button is
  // called after. Pinning a version and sharing one are different things:
  // the button pins, and only a shared resume has anything to "publish" to.
  // Unknown reads as private, the common case, so the label settles rather
  // than flips for most people.
  const [isPublic, setIsPublic] = useState(false);
  const [family, setFamily] = useState<ResumeFamily | null>(null);
  const [tailorDialogOpen, setTailorDialogOpen] = useState(false);
  const [creatingTailor, setCreatingTailor] = useState(false);
  const [tailorError, setTailorError] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deletingResume, setDeletingResume] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [memoryLoaded, setMemoryLoaded] = useState(false);
  const [busyMemoryId, setBusyMemoryId] = useState<string | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  // The import turn's own card. Null once no import has ever been started
  // for this resume (plain `create` resumes, and any `upload` resume whose
  // import already finished before this mount's `getImport` call lands --
  // the backend still returns that last snapshot, so "done" renders too).
  const [importSnapshot, setImportSnapshot] = useState<ImportSnapshot | null>(null);
  const [tailorSnapshot, setTailorSnapshot] = useState<TailorRunSnapshot | null>(null);
  const [tailorActionPending, setTailorActionPending] = useState(false);
  // Whether this mount has already resolved "is there a run?". The start
  // below must not fire before the answer arrives, or a reload mid-pass
  // starts a second one.
  const [tailorCheckedFor, setTailorCheckedFor] = useState<string | null>(null);
  const [importElapsedSeconds, setImportElapsedSeconds] = useState(0);
  // A Retry/Resume click in flight. Keeps the card's button inert, so a
  // double click cannot race itself into the 409 the backend answers.
  const [importActionPending, setImportActionPending] = useState(false);
  const router = useRouter();
  const skipTimers = useRef(true);
  const sendingRef = useRef(false);
  const pendingTailorRef = useRef<string | null>(null);
  // Replaces kickstartedRef: the old flag lived only in this component, so a
  // reload lost it and the "has this tailor been opened" question fell back to
  // `messages.length > 0` -- which the orphaned user message satisfied by
  // itself. The durable answer is the run row; this ref only guards the
  // renders between starting one and its snapshot arriving.
  const tailorStartedRef = useRef(false);
  const tailorAbortRef = useRef<AbortController | null>(null);
  const savePromptedRef = useRef(false);
  const previewObjectUrls = useRef<string[]>([]);
  // How much of the running import this mount has taken in: the `applied`
  // count whose source landed in `source`, the `version` whose transcript was
  // read back, and whether an adopt is still owed. A ref rather than state
  // because every save path has to read it at call time -- `outstanding` goes
  // up and comes down inside one `onImportSnapshot` call, between renders.
  const importCursorRef = useRef(emptyImportCursor());
  // The same cursor for the tailor pass. Separate from the import's because
  // the two runs can only ever be consecutive, never concurrent, but they
  // track different hashes and a shared one would carry debt across them.
  const tailorCursorRef = useRef(emptyTailorCursor());
  // The stream this mount opened, so a remount (or a retry) aborts the old
  // one before starting a new one rather than leaving two running.
  const importAbortRef = useRef<AbortController | null>(null);
  const deltaBufferRef = useRef("");
  const deltaTimerRef = useRef<number | null>(null);
  const reasoningBufferRef = useRef("");
  const reasoningTimerRef = useRef<number | null>(null);

  // Deltas arrive token by token; committing each one would re-parse the whole
  // markdown body per token. Batch them, but never leave a tail unflushed.
  const flushDeltas = useCallback(() => {
    if (deltaTimerRef.current !== null) {
      window.clearTimeout(deltaTimerRef.current);
      deltaTimerRef.current = null;
    }
    const pending = deltaBufferRef.current;
    if (!pending) return;
    deltaBufferRef.current = "";
    setStreamingText((prev) => (prev ?? "") + pending);
  }, []);

  const pushDelta = useCallback(
    (text: string) => {
      deltaBufferRef.current += text;
      if (deltaTimerRef.current !== null) return;
      deltaTimerRef.current = window.setTimeout(flushDeltas, 50);
    },
    [flushDeltas],
  );

  const flushReasoning = useCallback(() => {
    if (reasoningTimerRef.current !== null) {
      window.clearTimeout(reasoningTimerRef.current);
      reasoningTimerRef.current = null;
    }
    const pending = reasoningBufferRef.current;
    if (!pending) return;
    reasoningBufferRef.current = "";
    setReasoningText((prev) => (prev ?? "") + pending);
  }, []);

  const pushReasoning = useCallback(
    (text: string) => {
      reasoningBufferRef.current += text;
      if (reasoningTimerRef.current !== null) return;
      reasoningTimerRef.current = window.setTimeout(flushReasoning, 50);
    },
    [flushReasoning],
  );

  const clearReasoning = useCallback(() => {
    if (reasoningTimerRef.current !== null) {
      window.clearTimeout(reasoningTimerRef.current);
      reasoningTimerRef.current = null;
    }
    reasoningBufferRef.current = "";
    setReasoningText(null);
  }, []);

  const clearStream = useCallback(() => {
    if (deltaTimerRef.current !== null) {
      window.clearTimeout(deltaTimerRef.current);
      deltaTimerRef.current = null;
    }
    deltaBufferRef.current = "";
    setStreamingText(null);
  }, []);

  // Cancel a pending flush if the editor unmounts mid-stream.
  useEffect(
    () => () => {
      if (deltaTimerRef.current !== null) window.clearTimeout(deltaTimerRef.current);
      if (reasoningTimerRef.current !== null) window.clearTimeout(reasoningTimerRef.current);
    },
    [],
  );

  const setPreviewPages = useCallback((preview: PreviewPages) => {
    for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    const urls = preview.pages.map((svg) =>
      URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })),
    );
    previewObjectUrls.current = urls;
    setPreviewUrls(urls);
    setTextLayers(preview.textLayers);
    setBands(preview.bands);
  }, []);

  /**
   * Whether a preview fetch should ask for the highlight bands.
   *
   * Only the fields tab draws them, and they cost the server a third typst subprocess
   * (`typst eval`) plus two projection parses -- on an endpoint the autosave calls
   * 400ms after every keystroke burst in the Typst tab, where the band cache misses
   * every time because the source just changed. Read through a ref so `refreshPreview`
   * keeps one identity: half the save paths depend on it.
   */
  const wantBandsRef = useRef(false);
  useEffect(() => {
    wantBandsRef.current = activeTab === "fields";
  }, [activeTab]);

  const refreshPreview = useCallback(async () => {
    setPreviewPages(await getPreviewPages(resumeId, wantBandsRef.current));
    setCompileError(null);
  }, [resumeId, setPreviewPages]);

  /**
   * One field edit.
   *
   * `/edit` is the compile gate too: it answers with the new source, projection and
   * preview, or refuses and changes nothing. Nothing is PUT here -- the server has
   * already written it. Which projection the intent was built from, and when, is
   * `createEditQueue`'s business.
   */
  const sendEdit = useCallback(
    async (body: IntentBody): Promise<EditResult> => {
      const controller = new AbortController();
      // A legitimate `/edit` can take ~90s -- three 30s typst timeouts -- but a request
      // that never answers must not hold the source for the life of the page: while
      // one is outstanding, `mayPutSource` refuses the autosave and `flushSave` both,
      // and the handover sits on its loading line. Bounded, so the worst case is one
      // lost commit instead of an editor that never saves again.
      const bail = window.setTimeout(() => controller.abort(), EDIT_TIMEOUT_MS);
      try {
        const out = await postEdit(resumeId, body, controller.signal);
        // The guard runs both ways. A commit starts on blur, so the person can be
        // typing in the Typst tab by the time it answers -- and the autosave is held
        // off while fields owns the source, so that typing is in this buffer and
        // nowhere else. Adopting would delete it from under the caret with no notice.
        // Their typing is in no other copy; the field edit is in a revision either
        // way, so the buffer wins and the collision is said out loud.
        if (sourceRef.current !== savedRef.current) {
          setStatus(t("fieldsEditCollided"));
          return out;
        }
        adoptServerSource(out.typstSource);
        setUpdatedAt(new Date().toISOString());
        setUnpublishedChanges(true);
        // The edit compiled -- that is what let it commit -- so an empty page list is
        // a half-read answer, not a document with no pages. Keeping the preview that
        // is already on screen beats blanking it.
        if (out.preview.pages.length > 0) setPreviewPages(out.preview);
        setCompileError(null);
        return out;
      } finally {
        window.clearTimeout(bail);
      }
    },
    [adoptServerSource, resumeId, setPreviewPages, t],
  );

  // Read through a ref so the queue below never has to be rebuilt when this closure
  // changes identity -- it is created once per editor mount and holds the projection
  // the next commit is built from.
  const sendEditRef = useRef(sendEdit);
  useEffect(() => {
    sendEditRef.current = sendEdit;
  }, [sendEdit]);

  const raiseEdits = useCallback(() => {
    editsInFlightRef.current += 1;
    setEditsInFlight(editsInFlightRef.current);
  }, []);

  const releaseEdits = useCallback(() => {
    editsInFlightRef.current -= 1;
    setEditsInFlight(editsInFlightRef.current);
  }, []);

  /**
   * The field edit queue: one commit at a time, each built from the answer to the one
   * before it. `createEditQueue` holds that projection and publishes it itself, which
   * is the part that has to be true (see its docstring and `edit-queue.test.ts`).
   *
   * It lives in the shell for the same reason the counter does: both have to outlive a
   * tab switch. A queue that died with the panel would let a remount race the commit
   * still settling from the mount before it, and the counter is what keeps the shell's
   * autosave quiet for the whole queued span.
   */
  // `useState` with a lazy initialiser, not `useMemo`: this holds the projection the
  // next commit is built from, and "created exactly once per mount" has to be a
  // guarantee rather than a cache hint. The setter is deliberately unused.
  const [editQueue] = useState(() =>
    createEditQueue<Projection, IntentBody, EditResult>(
      (intent) => sendEditRef.current(intent),
      { onStart: raiseEdits, onSettle: releaseEdits },
    ),
  );

  /**
   * Wait until no field commit is in flight. False if one is still going.
   *
   * For the writers that are neither the autosave nor `/edit` and cannot be refused --
   * restoring a revision is an explicit act on the whole document and should win over a
   * field edit, but it must not land *while* one is being applied, or the two writes
   * arrive in an order nobody chose and the result is neither the restored revision nor
   * the edited one.
   *
   * Bounded on purpose. `/edit` compiles, and a hung request would otherwise leave the
   * caller waiting forever; a refusal the person can retry beats a button that never
   * comes back.
   */
  const drainEdits = useCallback(async (): Promise<boolean> => {
    const until = Date.now() + EDIT_DRAIN_TIMEOUT_MS;
    while (editsInFlightRef.current > 0) {
      if (Date.now() >= until) return false;
      await new Promise((resolve) => window.setTimeout(resolve, EDIT_DRAIN_POLL_MS));
    }
    return true;
  }, []);

  const refreshMemory = useCallback(async () => {
    try {
      setMemory(await listMemory(resumeId));
      setMemoryLoaded(true);
    } catch {
      /* memory is optional; cards stay pending */
    }
  }, [resumeId]);

  const refreshAts = useCallback(async () => {
    const report = await getAtsReport(resumeId);
    setAts(report);
  }, [resumeId]);

  /**
   * Re-read the whole transcript from the server.
   *
   * For the writers that are not turns: an import runner, a tailor runner and
   * a template switch all write `chat_messages` rows server-side that nothing
   * streams to this mount. `setMessages` is otherwise only called at mount
   * and inside `sendUserChat`, so without this they are invisible until a
   * reload.
   *
   * The mount's own read is deliberately NOT routed through here. It lives
   * inside the big load effect and its `setMessages` is gated on that
   * effect's `cancelled` flag; a callback cannot see that flag, so moving it
   * would let a slow read from the previous resume land on the new one.
   */
  const refreshMessages = useCallback(async () => {
    try {
      const history = await getChatMessages(resumeId);
      setMessages(history.messages.map(withMessageId));
      setContextCut(history.contextCut);
    } catch {
      /* a later frame re-reads it, and so does a reload */
    }
  }, [resumeId]);

  const failCompile = useCallback((err: unknown, fallback: string) => {
    const detail = typstCompileDetail(err);
    if (detail) {
      setCompileError(detail);
      setStatus(fallback);
      return;
    }
    if (err instanceof ApiError && (err.status === 400 || err.status === 500 || err.status === 504)) {
      setCompileError(err.message || fallback);
      setStatus(fallback);
      return;
    }
    setStatus(fallback);
  }, []);

  // The trio every server-driven source change needs: repaint the preview,
  // recompile the PDF, and refresh ATS against it. Shared by the chat
  // `source` event and the import snapshot handler, so "the preview grows
  // section by section" is one code path, not two that can drift apart.
  const refreshAfterSourceChange = useCallback(async () => {
    try {
      await refreshPreview();
      try {
        await compileResume(resumeId, "pdf");
        await refreshAts();
      } catch (err) {
        failCompile(err, t("previewError"));
      }
    } catch (err) {
      failCompile(err, t("previewError"));
    }
  }, [failCompile, refreshAts, refreshPreview, resumeId, t]);

  // Kept in a ref so the stream effect below does not need to restart every
  // time this identity changes -- the same reason `sendUserChatRef` exists.
  const refreshAfterSourceChangeRef = useRef(refreshAfterSourceChange);
  useEffect(() => {
    refreshAfterSourceChangeRef.current = refreshAfterSourceChange;
  }, [refreshAfterSourceChange]);

  /**
   * Re-read the résumé and adopt what the server actually holds.
   *
   * The fields tab asks for this when it cannot tell whether its own edit landed (the
   * request timed out) or when the document was written from outside this page. The
   * panel refetches its own projection either way, but nothing moved `source` or
   * `savedSource` -- so the Typst tab still showed the **pre-edit** document, and the
   * first keystroke there armed a `PUT` of it. `put_resume` carries no base hash and
   * sets the source unconditionally, so an applied edit went with no error anywhere.
   *
   * `skipTimers` while the buffer moves, like the import and chat adopts: adopting is
   * not an edit and must not arm the autosave. Typing already in the buffer wins, as it
   * does in `sendEdit` -- it exists in no other copy, while the edit is in a revision.
   */
  const resyncSource = useCallback(async () => {
    try {
      const fresh = await getResume(resumeId);
      if (sourceRef.current !== savedRef.current) {
        setStatus(t("fieldsEditCollided"));
        return;
      }
      skipTimers.current = true;
      adoptServerSource(fresh.typst_source || "");
      setUpdatedAt(fresh.updated_at || "");
      setUnpublishedChanges(fresh.unpublished_changes ?? true);
      try {
        await refreshAfterSourceChangeRef.current();
      } finally {
        skipTimers.current = false;
      }
    } catch {
      /* The panel has already said what happened and refetched its projection. A
         failed re-read leaves the buffer where it was, which is the state this is
         trying to improve on, not a worse one. */
    }
  }, [adoptServerSource, resumeId, t]);

  // One import snapshot, applied to the editor.
  //
  // A snapshot whose `source_hash` differs from the one this editor adopted
  // means the resume's source on the server is no longer what `source`
  // holds -- see `importSourceChanged`, which also covers the two cases
  // where there is no hash to compare and the older `applied` rule stands
  // in (nothing adopted yet, or an API that predates the field).
  //
  // **Adopting the source is the load-bearing part, not the repaint.**
  // `refreshAfterSourceChange` reads everything back from the server, so the
  // preview, the PDF and the ATS report are all correct while `source` still
  // holds the pre-import starter template this mount loaded. Every write
  // path -- `flushSave` on leave/switch/new-tailor, `sendUserChat`'s pre-turn
  // PUT, and the 400ms autosave -- PUTs that state verbatim, which lands
  // inside `set_draft_source`'s 120s coalesce window and overwrites the
  // import's own revision in place, so history cannot recover it either.
  // Pulling the authoritative source in and guarding it with `skipTimers`,
  // exactly as the chat turn's `source` frame does, is what stops a finished
  // import being silently replaced by the template it started from.
  //
  // `importCursorRef` is what makes that safe rather than merely likely:
  //
  // - The adopt is marked outstanding **before** the fetch and cleared only
  //   once `setSource` has run, so `importBlocksSave` refuses every write for
  //   exactly as long as this buffer is behind the server's -- including the
  //   two round trips after the terminal frame has already flipped
  //   `importRunning` off.
  // - The cursor advances only once the adopt lands, so a transient
  //   `getResume` failure is genuinely retried by the next frame. On the
  //   terminal frame there is no next frame and nothing retries: the flag
  //   then stays up and the save paths keep refusing for the rest of the
  //   session. That costs the person a stale preview until they reload; the
  //   alternative cost them the import.
  // - The adopt runs **before** the transcript read, because a round trip
  //   between the two is a window in which the run is no longer "running"
  //   and `source` is still the pre-import template.
  const onImportSnapshot = useCallback(
    async (next: ImportSnapshot) => {
      setImportSnapshot(next);
      const plan = planImportAdopt(next, importCursorRef.current);
      if (plan.adoptSource) {
        importCursorRef.current = { ...importCursorRef.current, outstanding: true };
        let adopted = false;
        try {
          const fresh = await getResume(resumeId);
          skipTimers.current = true;
          adoptServerSource(fresh.typst_source || "");
          setUpdatedAt(fresh.updated_at || "");
          setUnpublishedChanges(fresh.unpublished_changes ?? true);
          adopted = true;
          try {
            await refreshAfterSourceChangeRef.current();
          } finally {
            skipTimers.current = false;
          }
        } catch {
          /* the import itself is unaffected -- it writes the source
             server-side. The cursor stays in debt, which both asks the next
             frame to retry and keeps every save path from writing this stale
             buffer over the run's own work. */
        } finally {
          importCursorRef.current = nextImportCursor(
            next,
            importCursorRef.current,
            adopted ? "adopted" : "failed",
          );
        }
      } else {
        importCursorRef.current = nextImportCursor(next, importCursorRef.current, "not-needed");
      }
      // The runner writes its `tool` and `assistant` rows straight to the
      // database -- nothing streams them, and `setMessages` is otherwise only
      // called at mount and inside `sendUserChat`. Without this the person
      // watches the card above an empty transcript and never sees the agent's
      // closing summary unless they reload. `version` is the signal: it is
      // bumped by every write the runner makes, including the ones that
      // persist those rows.
      if (plan.readTranscript) await refreshMessages();
    },
    [adoptServerSource, refreshMessages, resumeId],
  );

  // Opens the live stream for a running import and feeds every snapshot
  // through `onImportSnapshot`.
  const attachImportStream = useCallback(() => {
    importAbortRef.current?.abort();
    const controller = new AbortController();
    importAbortRef.current = controller;
    void streamImport(resumeId, onImportSnapshot, controller.signal).catch(() => {
      /* a dropped connection just stops updating the card; a reload
         re-attaches via the mount effect below */
    });
  }, [onImportSnapshot, resumeId]);

  // On mount: pick up whatever import run (if any) this resume has, and
  // keep following it if it is still running. This is what makes a reload
  // mid-import re-attach instead of losing the card.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await getImport(resumeId);
        if (cancelled || !snap) return;
        // Seeded, not replayed: this mount has just loaded the source and the
        // transcript, so the snapshot it starts from is already accounted for.
        importCursorRef.current = seedImportCursor(snap);
        setImportSnapshot(snap);
        if (snap.status === "running") attachImportStream();
      } catch {
        /* import status is optional; the editor works without it */
      }
    })();
    return () => {
      cancelled = true;
      importAbortRef.current?.abort();
    };
  }, [attachImportStream, resumeId]);

  // The tailor pass, in the same three pieces as the import: follow a live
  // run, pick one up on mount, and start one when there is none.

  // One tailor snapshot, applied to the editor.
  //
  // This used to be `(snap) => setTailorSnapshot(snap)` and nothing else,
  // which was the bug: the card was mirrored from the import, the handler was
  // not. The runner writes its `tool` and `assistant` rows straight to the
  // database and the pass's SSE stream carries only the run snapshot, so the
  // person watched a spinning card above an empty transcript and saw the
  // agent's work only after a reload.
  //
  // The ordering below is the import's, for the import's reasons: the adopt
  // is marked outstanding *before* the fetch so every save path refuses while
  // this buffer is behind the server, the cursor advances only once the adopt
  // lands so a transient failure is retried by the next frame, and the adopt
  // runs *before* the transcript read, because a round trip between the two
  // is a window in which the run already reads "done" while `source` is
  // stale.
  const onTailorSnapshot = useCallback(
    async (next: TailorRunSnapshot) => {
      setTailorSnapshot(next);
      const plan = planTailorAdopt(next, tailorCursorRef.current);
      if (plan.adoptSource) {
        tailorCursorRef.current = { ...tailorCursorRef.current, outstanding: true };
        let adopted = false;
        try {
          const fresh = await getResume(resumeId);
          skipTimers.current = true;
          adoptServerSource(fresh.typst_source || "");
          setUpdatedAt(fresh.updated_at || "");
          setUnpublishedChanges(fresh.unpublished_changes ?? true);
          adopted = true;
          try {
            await refreshAfterSourceChangeRef.current();
          } finally {
            skipTimers.current = false;
          }
        } catch {
          /* the pass is unaffected -- it writes server-side. The cursor stays
             in debt, which asks the next frame to retry and keeps every save
             path from writing this stale buffer over the run's own work. */
        } finally {
          tailorCursorRef.current = nextTailorCursor(
            next,
            tailorCursorRef.current,
            adopted ? "adopted" : "failed",
          );
        }
      } else {
        tailorCursorRef.current = nextTailorCursor(next, tailorCursorRef.current, "not-needed");
      }
      if (plan.readTranscript) await refreshMessages();
    },
    [adoptServerSource, refreshMessages, resumeId],
  );

  const attachTailorStream = useCallback(() => {
    tailorAbortRef.current?.abort();
    const controller = new AbortController();
    tailorAbortRef.current = controller;
    void streamTailorRun(resumeId, onTailorSnapshot, controller.signal).catch(() => {
      /* a dropped connection just stops updating the card; a reload
         re-attaches via the mount effect below */
    });
  }, [onTailorSnapshot, resumeId]);

  // On mount: pick up whatever pass this resume has and keep following it.
  // This is the whole point of the run row -- a reload mid-pass re-attaches
  // instead of leaving an unanswered message nothing would ever retry.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await getTailorRun(resumeId);
        if (cancelled) return;
        setTailorSnapshot(snap);
        // This mount read the source and the transcript itself, so the
        // snapshot it just saw is already accounted for -- seeding stops the
        // first live frame re-reading both for nothing.
        if (snap) tailorCursorRef.current = seedTailorCursor(snap);
        if (snap && snap.status === "running" && !snap.stale) attachTailorStream();
      } catch {
        /* the pass is optional; the editor works without it */
      } finally {
        if (!cancelled) setTailorCheckedFor(resumeId);
      }
    })();
    return () => {
      cancelled = true;
      tailorAbortRef.current?.abort();
    };
  }, [attachTailorStream, resumeId]);

  const startTailor = useCallback(async () => {
    setTailorActionPending(true);
    try {
      const snap = await startTailorRun(resumeId);
      setTailorSnapshot(snap);
      // A pass that has just been created has written nothing yet, so this
      // snapshot is accounted for too -- and seeding here also clears any
      // debt a previous pass on this mount left behind.
      tailorCursorRef.current = seedTailorCursor(snap);
      attachTailorStream();
    } catch {
      // Ours, not the backend's sentence: "a tailor pass is already running
      // for resume <uuid>" is not something to show anyone. A 409 here means
      // another tab already started it, which the stream will show anyway.
      toast(t("tailorRunActionError"));
    } finally {
      setTailorActionPending(false);
    }
  }, [attachTailorStream, resumeId, t, toast]);

  // The pass that opens a tailor, started once the mount knows there is none.
  // `tailorStartedRef` guards the window between this firing and the snapshot
  // landing in state, which is more than one render.
  useEffect(() => {
    if (tailorStartedRef.current) return;
    if (!shouldStartTailorRun({ kind, loaded, messageCount: messages.length, snapshot: tailorSnapshot })) {
      return;
    }
    // Only once this mount has actually asked about THIS resume. A boolean
    // would still read true from the previous one for a render.
    if (tailorCheckedFor !== resumeId) return;
    tailorStartedRef.current = true;
    const handle = window.setTimeout(() => {
      setLeftTab("chat");
      saveEditorTab(resumeId, "chat");
      void startTailor();
    }, 0);
    return () => window.clearTimeout(handle);
  }, [kind, loaded, messages.length, resumeId, startTailor, tailorCheckedFor, tailorSnapshot]);

  const tailorRunView = useMemo(
    () => (tailorSnapshot ? toTailorRunView(tailorSnapshot) : undefined),
    [tailorSnapshot],
  );
  const tailorRunning = tailorRunBlocksComposer(tailorRunView);

  // When the run opened and whether it is still going -- the only two things
  // the clock below needs. Both primitives, so a snapshot that changes
  // nothing else does not restart the interval: depending on the snapshot
  // object tore the 1s timer down and rebuilt it about two and a half times
  // a second, which is the poll rate, not the tick rate.
  const importStartedMs = useMemo(
    () => (importSnapshot ? importTurnStartsAt(importSnapshot) : null),
    [importSnapshot],
  );
  const importRunStatus = importSnapshot?.status;

  // Ticks the footer's elapsed-time readout while the run is live; holds
  // still once it is not, so "12s elapsed" does not keep climbing after the
  // turn has already finished or failed.
  useEffect(() => {
    if (importStartedMs === null) return;
    const tick = () =>
      setImportElapsedSeconds(Math.max(0, Math.round((Date.now() - importStartedMs) / 1000)));
    tick();
    if (importRunStatus !== "running") return;
    const handle = window.setInterval(tick, 1000);
    return () => window.clearInterval(handle);
  }, [importRunStatus, importStartedMs]);

  // Both the stale card's "Resume" and the failed card's "Retry" call the
  // same endpoint -- the backend tells the two apart by what it finds on
  // the row, not by which button was pressed.
  const onImportAction = useCallback(async () => {
    setImportActionPending(true);
    try {
      const snap = await startImport(resumeId);
      // A run that has just started has written nothing yet, so this clears
      // any debt the previous run left behind as well.
      importCursorRef.current = seedImportCursor(snap);
      setImportSnapshot(snap);
      attachImportStream();
    } catch {
      // Swallowing this entirely meant a 409 -- or anything else -- produced
      // no feedback at all, so the button read as doing nothing. The toast
      // is ours, not the backend's sentence: "an import is already running
      // for resume <uuid>" is not something to show anyone.
      toast(t("importActionError"));
    } finally {
      setImportActionPending(false);
    }
  }, [attachImportStream, resumeId, t, toast]);

  const importView = useMemo(
    () => (importSnapshot ? toImportView(importSnapshot) : undefined),
    [importSnapshot],
  );
  // Where the import's own turn ends, so the card renders at that boundary
  // rather than below every later message forever -- `getImport` keeps
  // returning the last run's snapshot, so a finished import's collapsed card
  // would otherwise stay pinned to the bottom of the pane on every visit.
  // What the settings panel shows. Derived rather than stored: the
  // `importStatus` state is read once from the resume row at mount and was
  // never updated again, so a run that finished during the session left it
  // reading "Pending" for the rest of it. A live snapshot is the better
  // answer whenever there is one -- it is the same row's status, fresher.
  const effectiveImportStatus = importSnapshot ? importStatusOf(importSnapshot) : importStatus;

  const importCardAt = useMemo(
    () =>
      importSnapshot
        ? importCardIndex(messages, importTurnStartsAt(importSnapshot))
        : undefined,
    [importSnapshot, messages],
  );
  // While a run is live the server owns `resume.typst_source`: it is being
  // rewritten section by section, and `source` here is only as fresh as the
  // last snapshot that adopted it. Read by `flushSave`.
  const importRunning = importView?.mode === "running";

  useEffect(() => {
    let live = true;
    // A failure here must not break the editor: no meter is better than no
    // editor, and the limit is enforced server-side either way.
    fetchUsage()
      .then((next) => {
        if (live) setUsage(next);
      })
      .catch(() => undefined);
    // Same rule: no picker is better than no editor. Deliberately not awaited
    // together with the balance -- either one failing must not cost the other.
    fetchModels()
      .then((next) => {
        if (live) setModels(next);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        try {
          const account = await getMe();
          setMe(account);
          if (account) {
            try {
              await claimResumes();
            } catch {
              /* claim is best-effort */
            }
          }
        } catch {
          /* auth check is optional */
          setMe(null);
        }
        const resume = await getResume(resumeId);
        if (cancelled) return;
        setTitle(resume.title || "");
        setKind(resume.kind);
        setCreatedAt(resume.created_at);
        setResumeSource(resume.source);
        setResumeLocale(resume.locale);
        setImportStatus(resume.import_status);
        adoptServerSource(resume.typst_source || "");
        setUpdatedAt(resume.updated_at || "");
        setUnpublishedChanges(resume.unpublished_changes ?? true);
        void getResumeFamily(resumeId)
          .then((next) => {
            if (!cancelled) setFamily(next);
          })
          .catch(() => {
            /* the family menu still offers a new tailor */
          });
        void listMemory(resumeId)
          .then((items) => {
            if (cancelled) return;
            setMemory(items);
            setMemoryLoaded(true);
          })
          .catch(() => {
            /* memory is optional; cards stay pending */
          });
        try {
          const history = await getChatMessages(resumeId);
          if (!cancelled) {
            setMessages(history.messages.map(withMessageId));
            setContextCut(history.contextCut);
          }
        } catch {
          /* chat history is optional */
        }
        if (cancelled) return;
        setLoaded(true);
        try {
          const preview = await getPreviewPages(resumeId, wantBandsRef.current);
          if (!cancelled) {
            setPreviewPages(preview);
            setCompileError(null);
          }
        } catch (err) {
          if (!cancelled) failCompile(err, t("previewError"));
        }
        try {
          const report = await getAtsReport(resumeId);
          if (!cancelled) setAts(report);
        } catch {
          /* ATS appears after a successful compile */
        }
      } catch {
        if (!cancelled) setLoadError(t("loadError"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adoptServerSource, failCompile, resumeId, setPreviewPages, t]);

  useEffect(() => {
    return () => {
      for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    };
  }, []);

  useEffect(() => {
    if (!loaded) return;
    if (skipTimers.current) return;
    // The third save path, and the one a person is most likely to take at the
    // worst possible moment. If the terminal frame's adopt failed, the Typst
    // tab is showing the pre-import starter template, the import looks lost,
    // and the obvious response is to start typing it back in -- 400ms later
    // this would PUT that template over the finished import. C1 again,
    // through the one door the other two guards left open.
    //
    // `importView` is in the deps so a save deferred here is picked up the
    // moment the block lifts. While the run is live, the next adopt replaces
    // `source` wholesale anyway, so typing into the Typst tab mid-import was
    // never going to survive -- this only stops it reaching the server first.
    // And if the adopt is outstanding for good (a failed re-read on the
    // terminal frame, with no later frame to retry on) the block never lifts
    // and the typing is never written. That is the intended trade: what it
    // would persist is a buffer we know does not match the server, and a
    // reload recovers both the import and a working editor.
    if (importBlocksSave(importView?.mode, importCursorRef.current)) return;
    // The same refusal for a tailor pass, which had none: the composer gate
    // stops a typed turn, but this timer is not a turn and it PUTs whatever
    // buffer this mount holds.
    if (tailorBlocksSave(tailorRunView?.mode, tailorCursorRef.current)) return;
    // The fourth save path, and the one that loses a person's writing without an error
    // of any kind: while the fields tab owns the source, this PUT would put this
    // mount's copy of `source` over the field edit the server has already applied, and
    // `/edit` has no way to notice. `sourceOwner` is in the deps, so a debounce armed
    // by the Typst tab is cancelled the moment the handover happens rather than firing
    // 400ms into the fields tab's turn.
    //
    // The second half is the handover back: ownership returns to the shell holding the
    // very bytes `/edit` answered with, and PUTting those again would cost a write and
    // a compile to change nothing -- and would do it at the one moment a commit may
    // still be settling.
    // While the fields tab is up, writing is the handover's job: either its flush is
    // in flight or fields owns the source outright. Without this, the 400ms timer the
    // last keystroke in the Typst tab armed survives the switch -- `sourceOwner` is
    // still "shell" until the flush lands -- and PUTs the very buffer the handover is
    // PUTting. Two writes, and two preview rounds of three typst subprocesses each.
    if (activeTab === "fields") return;
    if (!shellShouldPutSource({ owner: sourceOwner, source, savedSource })) return;
    const handle = window.setTimeout(async () => {
      try {
        setStatus(t("saving"));
        const saved = await putResumeSource(resumeId, { typst_source: source });
        setSavedSource(source);
        if (saved.updated_at) setUpdatedAt(saved.updated_at);
        if (typeof saved.unpublished_changes === "boolean") {
          setUnpublishedChanges(saved.unpublished_changes);
        }
        await refreshPreview();
        try {
          await refreshAts();
        } catch {
          /* ATS needs a successful PDF compile */
        }
        setStatus(t("saved"));
      } catch (err) {
        failCompile(err, t("previewError"));
      }
    }, 400);
    return () => window.clearTimeout(handle);
  }, [
    activeTab,
    failCompile,
    importView,
    tailorRunView,
    loaded,
    refreshAts,
    refreshPreview,
    resumeId,
    savedSource,
    source,
    sourceOwner,
    t,
  ]);

  useEffect(() => {
    if (!loaded) return;
    if (skipTimers.current) {
      skipTimers.current = false;
      return;
    }
    const handle = window.setTimeout(async () => {
      try {
        await compileResume(resumeId, "pdf");
        setCompileError(null);
        await refreshAts();
      } catch (err) {
        failCompile(err, t("compileError"));
      }
    }, 2000);
    return () => window.clearTimeout(handle);
  }, [failCompile, loaded, refreshAts, resumeId, source, t]);

  async function onDownload() {
    setDownloading(true);
    try {
      const blob = await exportPdf(resumeId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${title || "resume"}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
      try {
        await refreshAts();
      } catch {
        /* export still succeeded */
      }
      setCompileError(null);
      // A guest who just downloaded a PDF has seen the product work, which is the
      // honest moment to mention that the resume is only in this browser. Once per
      // editor session, never for a signed-in user, and never before this point.
      if (!signedIn && !savePromptedRef.current) {
        savePromptedRef.current = true;
        try {
          toast(t("guestSaveNudge"));
        } catch {
          /* the download is what mattered */
        }
      }
    } catch (err) {
      failCompile(err, t("compileError"));
    } finally {
      setDownloading(false);
    }
  }

  const sendUserChat = useCallback(
    async (
      text: string,
      file: File | null = null,
      options?: { preferFullSource?: boolean; template?: { name: string; version: string } },
    ) => {
    const trimmed = text.trim();
    if ((!trimmed && !file) || sendingRef.current) return;
    // A turn started while the import is still running is two agents editing
    // one resume: both write through `set_draft_source`, inside whose 120s
    // coalesce window the loser's sections disappear without a revision to
    // recover them. The composer is disabled for the same reason, but every
    // programmatic caller -- the tailor kickstart, a template, "fix with AI",
    // the compile-error question -- arrives here too, so the refusal belongs
    // here and not only in the UI.
    if (importRunning) return;
    // Same collision, same refusal: while the tailor pass is running it owns
    // `resume.typst_source` too. Every programmatic caller arrives here, so
    // the guard belongs here and not only in the UI.
    if (tailorRunning) return;
    // Every AI turn comes through here — kickstart, "fix with AI", the
    // compile-error question, a template. On a phone the turn would stream out
    // of sight unless the sheet comes up with it.
    setSheetOpen(true);
    sendingRef.current = true;
    setSending(true);
    // A fresh turn has narrated nothing yet. Reset here rather than on `done`
    // so a turn that dies without one still starts the next one clean.
    setTurnProgressed(false);
    setMessages((prev) => [
      ...prev,
      withMessageId({
        role: "user",
        content: formatUserAttachmentMessage(trimmed, file?.name ?? null),
        timestamp: new Date().toISOString(),
      }),
    ]);
    try {
      // The pre-turn PUT, skipped while an import's adopt is outstanding:
      // `source` is then known to be the older document, and sending it would
      // overwrite the sections the import wrote. The turn runs against the
      // server's copy instead, which is the one the person is looking at.
      // And for a tailor pass. The `if (tailorRunning) return` above looks
      // like it covers this, but that is `mode === "running"` only: a pass
      // that has finished with its adopt still outstanding walks straight
      // through it, and `source` is then known to be the older document.
      // And not while the fields tab owns the source: on a phone the tools sheet puts
      // the tab strip and "Ask AI" side by side, so a turn can start with a field
      // commit still settling, and this PUT would answer it with the older document.
      if (
        !importBlocksSave(importView?.mode, importCursorRef.current) &&
        !tailorBlocksSave(tailorRunView?.mode, tailorCursorRef.current) &&
        mayPutSource(currentOwner())
      ) {
        skipTimers.current = true;
        const saved = await putResumeSource(resumeId, { typst_source: source });
        setSavedSource(source);
        if (saved.updated_at) setUpdatedAt(saved.updated_at);
      }
    } catch (err) {
      skipTimers.current = false;
      const message = err instanceof ApiError ? err.message : t("chatError");
      setMessages((prev) => [
        ...prev,
        withMessageId({ role: "assistant", content: message, timestamp: new Date().toISOString() }),
      ]);
      sendingRef.current = false;
      setSending(false);
      return;
    } finally {
      skipTimers.current = false;
    }
    try {
      await sendChat(resumeId, trimmed, file, options, async (event) => {
        if (event.type === "context") {
          setContextCut({
            dropped: event.dropped,
            fromMessageId: event.from_message_id,
          });
          return;
        }
        if (event.type === "delta") {
          setChatUnavailable(false);
          setTurnProgressed(true);
          pushDelta(event.text);
          return;
        }
        if (event.type === "reasoning") {
          setChatUnavailable(false);
          pushReasoning(event.text);
          return;
        }
        if (event.type === "tool" || event.type === "assistant") {
          setChatUnavailable(false);
          setTurnProgressed(true);
          if (event.type === "assistant") {
            // The real message supersedes the provisional bubble. Clearing and
            // appending in one invocation keeps it to a single paint, so the
            // same text never shows twice. A `tool` event must NOT clear it:
            // by then the round's words have already been finalized by their
            // own assistant event, and clearing here would erase the next
            // round's opening instead.
            clearStream();
          }
          setMessages((prev) => [...prev, withMessageId(event.message)]);
          if (
            event.type === "tool" &&
            parseToolPayload(event.message.content)?.name === "propose_memory"
          ) {
            void refreshMemory();
          }
          return;
        }
        if (event.type === "quota") {
          // The person's own allowance, never telemetry -- see the backend's
          // quota_event. Arrives just before `done`, so this is the number the
          // database holds at the end of the turn.
          setUsage((prev) =>
            prev
              ? {
                  ...prev,
                  tier: event.tier,
                  reset_at: event.reset_at,
                  used_credits: event.used_credits,
                  limit_credits: event.limit_credits,
                  turns_used: event.turns_used,
                  turn_limit: event.turn_limit,
                  approx_edits_left: event.approx_edits_left,
                  remaining_credits:
                    event.limit_credits === null
                      ? null
                      : Math.max(0, event.limit_credits - event.used_credits),
                  turns_remaining:
                    event.turn_limit === null
                      ? null
                      : Math.max(0, event.turn_limit - event.turns_used),
                  exhausted:
                    (event.turn_limit !== null && event.turns_used >= event.turn_limit) ||
                    (event.limit_credits !== null && event.used_credits >= event.limit_credits),
                }
              : prev,
          );
          return;
        }
        if (event.type === "done") {
          // Safety net: a stream that ends mid-round must not strand a bubble.
          clearStream();
          clearReasoning();
          return;
        }
        if (event.type === "tailor_created") {
          pendingTailorRef.current = event.resume_id;
          return;
        }
        if (event.type === "source") {
          skipTimers.current = true;
          adoptServerSource(event.typst_source);
          setUpdatedAt(new Date().toISOString());
          setUnpublishedChanges(true);
          try {
            await refreshAfterSourceChange();
          } finally {
            skipTimers.current = false;
          }
          return;
        }
        if (event.type === "error") {
          clearStream();
          clearReasoning();
          if (event.status === 503) {
            setChatUnavailable(true);
            setMessages((prev) => [
              ...prev,
              withMessageId({
                role: "assistant",
                content: t("chatNotConfigured"),
                timestamp: new Date().toISOString(),
              }),
            ]);
            return;
          }
          setMessages((prev) => [
            ...prev,
            withMessageId({
              role: "assistant",
              content: event.detail || t("chatError"),
              timestamp: new Date().toISOString(),
            }),
          ]);
        }
      });
    } catch (err) {
      const limitHit = usageRefusalOf(err);
      if (limitHit) {
        showUsageRefusal(limitHit);
      } else if (err instanceof ApiError && err.status === 503) {
        setChatUnavailable(true);
        setMessages((prev) => [
          ...prev,
          withMessageId({
            role: "assistant",
            content: t("chatNotConfigured"),
            timestamp: new Date().toISOString(),
          }),
        ]);
      } else {
        const message = err instanceof ApiError ? err.message : t("chatError");
        setMessages((prev) => [
          ...prev,
          withMessageId({ role: "assistant", content: message, timestamp: new Date().toISOString() }),
        ]);
      }
    } finally {
      sendingRef.current = false;
      setSending(false);
      // A thrown ApiError skips the `done` event, so clear here too.
      clearStream();
      clearReasoning();
      const nextTailor = pendingTailorRef.current;
      pendingTailorRef.current = null;
      if (nextTailor) {
        saveEditorTab(nextTailor, "chat");
        router.push(`/editor/${nextTailor}`);
      }
    }
    },
    [
      adoptServerSource,
      clearReasoning,
      clearStream,
      currentOwner,
      importRunning,
      tailorRunning,
      importView,
      tailorRunView,
      pushDelta,
      pushReasoning,
      refreshAfterSourceChange,
      refreshMemory,
      resumeId,
      router,
      source,
      t,
    ],
  );
  const sendUserChatRef = useRef(sendUserChat);
  useEffect(() => {
    sendUserChatRef.current = sendUserChat;
  }, [sendUserChat]);

  // A resume created a moment ago opens on chat, so a first-timer meets the
  // composer instead of the starter template's Typst markup. Persist that so a
  // reload does not drop them back into the source; from here the user's own tab
  // choice takes over, which is why the handoff marker is consumed.
  useEffect(() => {
    if (!takePendingCreate(resumeId)) return;
    saveEditorTab(resumeId, "chat");
  }, [resumeId]);

  function onDraftChange(value: string) {
    setDraft(value);
    saveComposerDraft(resumeId, value);
  }

  /**
   * Why `flushSave` would write nothing, or null when it would write.
   *
   * `flushSave` answers `true` for three different outcomes -- wrote it, refused
   * because a run owns the source, refused because the fields tab does -- because
   * "nothing to flush" is not a failure for a caller that only wants to leave. The
   * handover does need the difference: it waits for `savedSource === source`, which a
   * refusal never reaches, so without this it would wait for ever.
   *
   * The refusals themselves: while an import or tailor run is live there is nothing of
   * the person's to flush, and writing our copy of `source` back would overwrite the
   * sections the agent has applied since the last snapshot we adopted -- and while an
   * adopt is outstanding our copy is known to be the older document, which outlasts the
   * run by however long the adopt takes to land (or forever, if it failed on the
   * terminal frame). The fields tab is the same shape: it writes through `/edit`, so
   * the server already has everything there is to flush, and this mount's `source` is
   * the older document for as long as a commit is in flight. Ownership is read from the
   * refs, not from a render's state: a click on Back blurs the field first, and that
   * blur starts a commit one event before the click handler runs.
   */
  const flushBlockedBy = useCallback((): "run" | "fields" | null => {
    if (importBlocksSave(importView?.mode, importCursorRef.current)) return "run";
    if (tailorBlocksSave(tailorRunView?.mode, tailorCursorRef.current)) return "run";
    if (!mayPutSource(currentOwner())) return "fields";
    return null;
  }, [currentOwner, importView, tailorRunView]);

  async function flushSave(): Promise<boolean> {
    if (flushBlockedBy()) return true;
    skipTimers.current = true;
    try {
      const saved = await putResumeSource(resumeId, { typst_source: source });
      setSavedSource(source);
      if (saved.updated_at) setUpdatedAt(saved.updated_at);
      return true;
    } catch {
      return false;
    } finally {
      skipTimers.current = false;
    }
  }

  // `flushSave` is a new function every render; the handover effect below must not
  // re-run (and re-flush) for that, so it reads the latest one from here.
  const flushSaveRef = useRef(flushSave);
  useEffect(() => {
    flushSaveRef.current = flushSave;
  });

  /**
   * Handing the source over to the fields tab.
   *
   * Anything typed into the Typst tab in the last 400ms has not been PUT yet, and the
   * projection the panel is about to fetch is of the source the *server* holds. So the
   * buffer is flushed first and `fieldsOpen` -- which is both the ownership flag and
   * the panel's mount condition -- only becomes true once the PUT has landed, because
   * it is `savedSource === source`. A panel mounted before that would offer a form
   * built from a document missing the person's last sentence, and their next field
   * edit would be written on top of it.
   *
   * If the flush fails the tab does not open at all, and says so: the only thing worse
   * than not opening it is opening it over writing that is still only in this browser.
   *
   * Nothing is flushed before `loaded`, where `source` is still "" -- a stored tab
   * puts us here on the very first render.
   *
   * Taking ownership back needs no code: leaving the tab makes `fieldsOpen` false by
   * itself, and `editsInFlight` holds the handover open until the last commit lands.
   */
  /**
   * Fetch the highlight bands once the fields tab is the one on screen.
   *
   * `refreshPreview` asks for them only while this tab is up, so the preview a page
   * load or a Typst-tab save left behind carries none and the panel would have nothing
   * to highlight with. Asked for once per source: an empty answer is a real answer -- a
   * document with no editable line, an eval that failed -- and must not be retried on
   * every render. An `/edit` answer always carries bands, so this is the open, not the
   * steady state.
   */
  const bandsAskedForRef = useRef<string | null>(null);
  useEffect(() => {
    if (activeTab !== "fields" || !loaded) return;
    if (savedSource !== source) return; // the handover below flushes first
    if (Object.keys(bands).length > 0 || bandsAskedForRef.current === source) return;
    bandsAskedForRef.current = source;
    void refreshPreview().catch(() => {
      /* The picture on screen is still the document; only the highlight is missing,
         and the panel draws nothing rather than drawing it in the wrong place. */
    });
  }, [activeTab, bands, loaded, refreshPreview, savedSource, source]);

  useEffect(() => {
    if (activeTab !== "fields" || !loaded || savedSource === source) return;
    const stayOnSource = () => {
      setStatus(t("switchSaveError"));
      setLeftTab("typst");
      saveEditorTab(resumeId, "typst");
    };
    // A flush that would write nothing never reaches `savedSource === source`, so the
    // tab would sit on its loading line forever. A field commit still settling comes
    // back here when it lands (`editsInFlight` is in the deps); an import or a tailor
    // pass owns the source for far longer than a tab switch should wait, so that one
    // is told, not waited on.
    const blocked = flushBlockedBy();
    if (blocked === "fields") return;
    if (blocked === "run") {
      stayOnSource();
      return;
    }
    let cancelled = false;
    void (async () => {
      const flushed = await flushSaveRef.current();
      if (cancelled) return;
      if (!flushed) {
        stayOnSource();
        return;
      }
      // Unlike the autosave, the chat and the restore paths, `flushSave` does not
      // repaint the preview -- its other callers are navigating away. Here the panel is
      // about to mount on a projection of the source this PUT just wrote, while the
      // SVGs and the bands on screen are of the one before it: if the Typst edit changed
      // the unit set, focusing a field would highlight the band an id used to name.
      await refreshPreview().catch(() => {
        /* The document is saved either way; `failCompile` is the autosave's job and the
           picture on screen is at worst one edit old. */
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTab, editsInFlight, flushBlockedBy, loaded, refreshPreview, resumeId,
      savedSource, source, t]);

  async function onLeaveEditor() {
    if (leaving) return;
    if (sendingRef.current) {
      // A turn, or a restore waiting for a field commit to land: both hold this flag,
      // and a Back button that does nothing at all for up to fifteen seconds reads as
      // a broken button rather than a busy one.
      setStatus(t("leaveBusy"));
      return;
    }
    setLeaving(true);
    try {
      // Leaving unmounts the 400ms autosave debounce, so anything typed inside
      // that window would never be written. Flush first, exactly as switching
      // between versions already does.
      if (!(await flushSave())) {
        setStatus(t("leaveSaveError"));
        return;
      }
      // The same rule the delete flow uses: /dashboard bounces a guest to
      // /login, which is a dead end that reads like a bug, so a guest goes to
      // the front page instead.
      router.push(signedIn ? "/dashboard" : "/");
    } finally {
      setLeaving(false);
    }
  }

  async function onSwitchResume(id: string) {
    if (sendingRef.current || id === resumeId) return;
    if (!(await flushSave())) {
      setStatus(t("switchSaveError"));
      return;
    }
    saveEditorTab(id, leftTab);
    router.push(`/editor/${id}`);
  }

  function openTailorDialog() {
    if (sendingRef.current) return;
    setTailorError(null);
    setTailorDialogOpen(true);
  }

  async function onCreateTailor(payload: TailorDraft) {
    if (creatingTailor) return;
    setCreatingTailor(true);
    setTailorError(null);
    try {
      if (!(await flushSave())) {
        setTailorError(t("switchSaveError"));
        return;
      }
      const tailor = await createTailor(resumeId, payload);
      setTailorDialogOpen(false);
      saveEditorTab(tailor.id, "chat");
      router.push(`/editor/${tailor.id}`);
    } catch (err) {
      setTailorError(err instanceof ApiError && err.message ? err.message : t("tailorCreateError"));
    } finally {
      setCreatingTailor(false);
    }
  }

  function openDeleteDialog() {
    if (sendingRef.current) return;
    setDeleteError(null);
    setDeleteDialogOpen(true);
  }

  async function onConfirmDelete() {
    if (deletingResume) return;
    setDeletingResume(true);
    setDeleteError(null);
    try {
      // Nothing left to autosave once the row is gone.
      skipTimers.current = true;
      await deleteResume(resumeId);
      saveComposerDraft(resumeId, "");
      const master = family && family.master.id !== resumeId ? family.master.id : null;
      router.replace(master ? `/editor/${master}` : signedIn ? "/dashboard" : "/");
      // The provider lives in the layout, so this outlives the navigation.
      toast(tCommon("deletedToast", { title }));
    } catch (err) {
      skipTimers.current = false;
      setDeleteError(err instanceof ApiError && err.message ? err.message : tCommon("deleteError"));
      setDeletingResume(false);
    }
  }

  async function onChat(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    const file = attachment;
    if ((!text && !file) || sendingRef.current) return;
    onDraftChange("");
    setAttachment(null);
    await sendUserChat(text, file);
  }

  function showUsageRefusal(limitHit: UsageRefusal) {
    // A card, not a chat bubble: this is the sign-up moment for a guest and
    // the matching moment for everyone else, and a bubble scrolls away. The
    // card itself names which limit was hit and what the next tier gives, and
    // shows a sign-in button when `tier === "guest"` or the refusal carries
    // `sign_in_required` (see UsageLimitCard).
    //
    // Shared with `onApplyTemplate`: a 429 is reachable from the template
    // switch too, exactly when the snapshot is stale and the subject is out of
    // credits -- the first switch a new person makes once their allowance is
    // spent. That used to fall into a bare `catch` and be reported as "could
    // not switch to that template", which is both wrong and unactionable.
    setRefusal(limitHit);
    // The refusal carries the balance that caused it, so the meter lands
    // on empty without a second request.
    setUsage((prev) =>
      prev
        ? {
            ...prev,
            tier: limitHit.tier,
            reset_at: limitHit.reset_at,
            used_credits: limitHit.used_credits,
            limit_credits: limitHit.limit_credits,
            turns_used: limitHit.turns_used,
            turn_limit: limitHit.turn_limit,
            exhausted: true,
          }
        : prev,
    );
  }

  async function onApplyTemplate(
    template: { name: string; version: string },
  ): Promise<AppliedTemplateNote | null> {
    // A second click during the `switchTemplate` await -- before `sending`
    // ever flips true -- would start a second switch whose own chat turn
    // then silently loses to `sendUserChat`'s `sendingRef` guard. Reusing
    // `onImportAction`'s own-flag pattern rather than `sendingRef` itself
    // because this flag must already be live before any chat turn exists.
    if (templateApplyPending || sendingRef.current) return null;
    setTemplateApplyPending(true);
    try {
      const result = await switchTemplate(resumeId, template.name, template.version);
      if (result.mode === "llm") {
        setLeftTab("chat");
        void sendUserChat(result.prompt, null, { preferFullSource: true, template });
        return null;
      }
      // The server did the rewrite itself, so there is no chat turn to run
      // -- just pick up the resume it already saved, the same refresh
      // `onRestoreHistory` does for a restored revision. The caller (the
      // template panel) shows its own inline note from the return value
      // here. The server also wrote a `role: "system"` row for this same
      // switch, but nothing streams it -- `setMessages` is otherwise only
      // called at mount and inside `sendUserChat` (see the import-runner
      // comment above) -- so without an explicit re-read here the person
      // would not see it in the transcript until a reload.
      await onRestoreHistory(result.resume);
      await refreshMessages();
      return {
        name: template.name,
        version: template.version,
        dropped: result.dropped,
        degraded: result.degraded,
      };
    } catch (err) {
      // A 429 is not a broken template: it is reachable here whenever the
      // snapshot is stale (so the switch needs one extraction call) and the
      // subject is out of credits -- the first switch a new person makes
      // once their allowance is spent, and for a guest `preflight` raises
      // `sign_in_required`. Routed to the same refusal card the chat path
      // uses, which names the limit and offers sign-in, rather than told
      // the template is broken.
      const limitHit = usageRefusalOf(err);
      if (limitHit) {
        // The card lives in the chat panel (EditorChatPanel renders it from
        // `usage.refusal`), and this ran from the template panel -- so switch
        // tabs the same way the `mode: "llm"` branch above does, or the person
        // would get no feedback at all.
        setLeftTab("chat");
        showUsageRefusal(limitHit);
        return null;
      }
      // Everything else: a 400 means the pair isn't on the committed support
      // list -- the picker should already only offer those, so this is a
      // mismatch between client and server state, not a routine failure.
      // Same swallow-and-toast as `onImportAction` for that and for a 404 or
      // a network failure: the backend's sentence isn't for the person, but
      // silence here reads as a dead button.
      toast(t("templateSwitchError"));
      return null;
    } finally {
      setTemplateApplyPending(false);
    }
  }

  /**
   * A revision restored from the history panel, which has already written it.
   *
   * No `drainEdits` here, unlike `onRestoreEdit` two hundred lines down: that one is a
   * PUT this component makes, carrying no hash, so an interleave there is the one case
   * nothing downstream would catch. This one is a server-side write that has already
   * happened by the time we hear about it, and `/edit` re-reads the source and
   * re-checks `body.hash` *after* its compile, so a field commit that loses the race
   * is refused with `stale_hash` rather than merged into the restored revision.
   */
  async function onRestoreHistory(saved: Resume) {
    skipTimers.current = true;
    adoptServerSource(saved.typst_source || "");
    if (saved.updated_at) setUpdatedAt(saved.updated_at);
    if (typeof saved.unpublished_changes === "boolean") {
      setUnpublishedChanges(saved.unpublished_changes);
    }
    try {
      await refreshPreview();
      try {
        await compileResume(resumeId, "pdf");
        await refreshAts();
      } catch (err) {
        failCompile(err, t("previewError"));
      }
    } catch (err) {
      failCompile(err, t("previewError"));
    } finally {
      skipTimers.current = false;
    }
  }

  // The settings panel fetches this too and reports it through onShareState,
  // but it is inside a tab that is unmounted until opened, so the toolbar
  // would be unlabelled until someone went looking.
  useEffect(() => {
    let cancelled = false;
    getResumeShare(resumeId)
      .then((state) => {
        if (!cancelled) setIsPublic(state.public);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [resumeId]);

  async function onPublish() {
    if (!signedIn || publishing) return;
    setPublishing(true);
    try {
      const state = await publishResume(resumeId);
      setUnpublishedChanges(state.unpublished_changes ?? false);
      setIsPublic(state.public);
    } catch {
      setStatus(isPublic ? t("publishError") : t("publishSaveError"));
    } finally {
      setPublishing(false);
    }
  }

  const onShareState = useCallback((state: ShareState) => {
    if (typeof state.unpublished_changes === "boolean") {
      setUnpublishedChanges(state.unpublished_changes);
    }
    setIsPublic(state.public);
  }, []);

  /**
   * One suggestion from a tailor pass, applied in the browser.
   *
   * It goes through `onRestoreEdit`, which is the editor's one client-side
   * source writer: it already holds the re-entrancy guard, suppresses the
   * autosave timers while it works, PUTs, and repaints the preview, the PDF
   * and the ATS report. Two writers would be two chances to lose a document.
   *
   * The refusal is the part that is new here. `onRestoreEdit` has no idea a
   * run may own the source, so without this a click during a live pass -- or
   * after one whose adopt never landed -- PUTs this mount's buffer over the
   * runner's own work, inside `set_draft_source`'s coalesce window where
   * history cannot get it back.
   */
  function onApplySuggestion(next: string) {
    if (importBlocksSave(importView?.mode, importCursorRef.current)) return;
    if (tailorBlocksSave(tailorRunView?.mode, tailorCursorRef.current)) return;
    void onRestoreEdit(next);
  }

  /**
   * Hand a suggestion back to the agent instead of applying it.
   *
   * For the ones whose `before` no longer matches the document, and the
   * message has to quote the change **in full**: tool rows are never
   * replayed to the model (`chat.py`'s `_history` filters to user and
   * assistant roles), so it has no memory of having proposed this. A message
   * saying "do the second suggestion" would reach a model that cannot see
   * any.
   */
  function onAskAboutSuggestion(action: TailorAction) {
    // An open suggestion has no pair to quote, so it asks about the point
    // itself. A rewrite quotes both halves, because that is the only way the
    // model learns what it proposed.
    const text =
      action.before !== null && action.after !== null
        ? t("actionAskPrompt", {
            title: action.title,
            before: action.before,
            after: action.after,
          })
        : t("actionAskOpenPrompt", { title: action.title });
    void sendUserChat(text);
  }

  /** One of the person's own canned replies, sent as an ordinary turn. */
  function onSendSuggestionReply(text: string) {
    void sendUserChat(text);
  }

  async function onRestoreEdit(previousSource: string) {
    if (sendingRef.current || previousSource === source) return;
    // `sending` is up for the whole of this, so the card's Restore button is already
    // inert while the wait below happens -- the restore reads as pending, which is what
    // a one-request wait should look like.
    sendingRef.current = true;
    setSending(true);
    try {
      // This is the one `putResumeSource` the fields tab cannot be allowed to block:
      // restoring a revision is deliberate and about the whole document, so it wins.
      // But it waits for the commit in flight first -- this PUT has no hash to check,
      // unlike `/edit`, so interleaving here is the one case nothing downstream would
      // catch.
      if (!(await drainEdits())) {
        setStatus(t("fieldsEditPending"));
        return;
      }
      skipTimers.current = true;
      setSource(previousSource);
      const saved = await putResumeSource(resumeId, { typst_source: previousSource });
      setSavedSource(previousSource);
      if (saved.updated_at) setUpdatedAt(saved.updated_at);
      if (typeof saved.unpublished_changes === "boolean") {
        setUnpublishedChanges(saved.unpublished_changes);
      }
      await refreshPreview();
      try {
        await compileResume(resumeId, "pdf");
        await refreshAts();
      } catch (err) {
        failCompile(err, t("previewError"));
      }
    } catch (err) {
      failCompile(err, t("previewError"));
    } finally {
      skipTimers.current = false;
      sendingRef.current = false;
      setSending(false);
    }
  }

  async function onCopyCompileError() {
    if (!compileError) return;
    await navigator.clipboard.writeText(compileError);
  }

  function onAskCompileError() {
    if (!compileError || sendingRef.current) return;
    const prompt = t("compileErrorAskPrompt", { error: compileError });
    setLeftTab("chat");
    void sendUserChat(prompt);
  }

  function onFixAts(name: AtsCheckName) {
    if (sendingRef.current) return;
    const prompt = tAts("fixPrompt", {
      name,
      check: tAts(`checks.${name}`),
      meaning: tAts(`meanings.${name}`),
    });
    setLeftTab("chat");
    void sendUserChat(prompt);
  }

  const onSwitchResumeRef = useRef(onSwitchResume);
  useEffect(() => {
    onSwitchResumeRef.current = onSwitchResume;
  });

  const runMemoryAction = useCallback(
    async (id: string, action: (itemId: string) => Promise<unknown>) => {
      setBusyMemoryId(id);
      setMemoryNotice(null);
      try {
        await action(id);
      } catch (err) {
        let notice = t("memoryError");
        if (err instanceof ApiError && err.status === 409) notice = t("memoryFull");
        if (err instanceof ApiError && err.status === 401) {
          // The session expired under us: drop the account, which also takes
          // the avatar out of the header rather than leaving it offering a
          // menu for a session that is gone.
          setMe(null);
          notice = t("memoryLogin");
        }
        setMemoryNotice(notice);
        setStatus(notice);
      } finally {
        setBusyMemoryId(null);
        await refreshMemory();
      }
    },
    [refreshMemory, t],
  );

  const familyActions = useMemo<FamilyCardActions>(
    () => ({
      memoryById: Object.fromEntries(memory.map((item) => [item.id, item])),
      memoryLoaded,
      signedIn,
      loginHref: `/login?next=/editor/${resumeId}`,
      busyMemoryId,
      onAcceptMemory: (id) => void runMemoryAction(id, acceptMemory),
      onDismissMemory: (id) => void runMemoryAction(id, dismissMemory),
      onOpenResume: (id) => void onSwitchResumeRef.current(id),
    }),
    [busyMemoryId, memory, memoryLoaded, resumeId, runMemoryAction, signedIn],
  );

  // Through refs, the same way `onSwitchResumeRef` reaches `familyActions`:
  // both handlers are plain functions redeclared every render, so holding
  // them directly would rebuild this object on every keystroke and re-render
  // every card in the transcript.
  const onApplySuggestionRef = useRef(onApplySuggestion);
  const onAskAboutSuggestionRef = useRef(onAskAboutSuggestion);
  const onSendSuggestionReplyRef = useRef(onSendSuggestionReply);
  useEffect(() => {
    onApplySuggestionRef.current = onApplySuggestion;
    onAskAboutSuggestionRef.current = onAskAboutSuggestion;
    onSendSuggestionReplyRef.current = onSendSuggestionReply;
  });

  const tailorActions = useMemo<TailorCardActions>(
    () => ({
      // The same two refusals the apply itself re-checks. Here so the button
      // looks inert rather than silently doing nothing.
      disabled:
        sending ||
        importBlocksSave(importView?.mode, importCursorRef.current) ||
        tailorBlocksSave(tailorRunView?.mode, tailorCursorRef.current),
      onApply: (next) => onApplySuggestionRef.current(next),
      onAsk: (action) => onAskAboutSuggestionRef.current(action),
      onSend: (text) => onSendSuggestionReplyRef.current(text),
    }),
    [importView?.mode, sending, tailorRunView?.mode],
  );

  // The dock's one affordance: raise the sheet on chat with the caret already
  // in the composer, so asking for an edit is a single tap from the preview.
  const askAi = useCallback(() => {
    setLeftTab("chat");
    saveEditorTab(resumeId, "chat");
    setSheetOpen(true);
    setChatFocusKey((key) => key + 1);
  }, [resumeId]);

  const familyView = useMemo<ResumeFamily | null>(() => {
    if (!family || !title) return family;
    const rename = <T extends { id: string; title: string }>(member: T): T =>
      member.id === resumeId ? { ...member, title } : member;
    return { master: rename(family.master), tailors: family.tailors.map(rename) };
  }, [family, resumeId, title]);

  const usagePanel = useMemo(() => {
    const countCapped = usage?.turn_limit != null;
    const nextTier = refusal?.next_tier;
    return {
      usage,
      refusal,
      onDismissRefusal: () => setRefusal(null),
      labels: {
        meter: countCapped
          ? t("usageMeterEdits", {
              used: usage?.turns_used ?? 0,
              limit: usage?.turn_limit ?? 0,
            })
          : t("usageMeterCredits", {
              used: usage?.used_credits ?? 0,
              limit: usage?.limit_credits ?? 0,
            }),
        reset: t("usageResets"),
        limitTitle: t("usageLimitTitle"),
        offer: nextTier
          ? nextTier.name === "registered"
            ? t("usageOfferSignIn", { credits: nextTier.credits ?? 0 })
            : t("usageOfferMatching", { credits: nextTier.credits ?? 0 })
          : null,
        limitReset: t("usageLimitReset"),
        signIn: t("usageSignIn"),
        settings: t("usageOpenSettings"),
        learnMore: t("usageLearnMore"),
        dismiss: t("usageDismiss"),
      },
    };
  }, [refusal, t, usage]);

  /**
   * Switching model. Optimistic on the id and authoritative on the result:
   * the dropdown closes on the click, and the table it then shows is the one
   * the server stored. A refusal (a guest reaching for a model their tier may
   * not have) rolls back to whatever the server says, rather than leaving the
   * picker claiming a model the next turn would not use.
   *
   * The balance is deliberately NOT refetched. It used to be, because the
   * meter quoted an estimate of edits left that depended on the chosen model
   * -- so switching changed the number without any spend. The meter now shows
   * credits actually spent, which a model switch cannot move, so the refetch
   * would be a request that could only return what the client already has.
   */
  const onSelectModel = useCallback(
    async (model: string) => {
      setModels((prev) => (prev ? { ...prev, selected: model } : prev));
      try {
        const next = await putSelectedModel(model);
        setModels(next);
      } catch {
        await fetchModels()
          .then(setModels)
          .catch(() => undefined);
      }
    },
    [],
  );

  const modelPanel = useMemo(() => {
    /* The picker deliberately carries no "about N edits left" per row.
     *
     * It used to, scaled from this person's balance by each model's cost
     * weight -- which inherits the flaw in that balance figure: it divides by
     * their own trailing cost per turn, so right after a model switch it is
     * badly wrong in whichever direction they switched. Measured live on
     * 2026-10-29: 43 edits quoted for Haiku against its published 793.
     *
     * A number that moves because of what you picked *last* is the worst
     * thing to put next to the control for picking the next one. The relative
     * cost chip stays, because that is a published rate and does not depend
     * on history, and the full table is a click away under the menu. */
    return {
      table: models,
      onSelect: onSelectModel,
      labels: {
        title: t("modelPickerTitle"),
        heading: t("modelPickerHeading"),
        cost: (row: ModelRow) => t("modelCost", { weight: row.cost_weight }),
        locked: (row: ModelRow) =>
          row.min_tier === "matching"
            ? t("modelLockedMatching")
            : t("modelLockedRegistered"),
        isDefault: t("modelIsDefault"),
        learnMore: t("modelLearnMore"),
      },
      footer: (
        <Link href="/usage" className="underline underline-offset-2">
          {t("modelLearnMore")}
        </Link>
      ),
    };
  }, [models, onSelectModel, t]);

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
        <p className="text-sm text-destructive">{loadError}</p>
      </div>
    );
  }

  if (!loaded) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      </div>
    );
  }

  // Copy lives here, as it does for every other label the chat panel takes.
  // Exactly one side of a tier is capped -- a guest on a turn count, an account
  // on cost -- so the meter string comes from whichever is set.
  const chatEmptyHint = chatUnavailable ? t("chatNotConfigured") : t("chatEmpty");
  // Openers for a master whose chat has never been used. A tailor gets none: its
  // kickstart already opens with a score against the target role. These fill the
  // composer and stop there, so chat stays something the user chooses to use.
  const chatSuggestions: ChatSuggestion[] | undefined =
    chatUnavailable || kind === "tailor"
      ? undefined
      : [
          { key: "fill", label: t("suggestFillLabel"), prompt: t("suggestFillPrompt") },
          { key: "bullets", label: t("suggestBulletsLabel"), prompt: t("suggestBulletsPrompt") },
          { key: "ats", label: t("suggestAtsLabel"), prompt: t("suggestAtsPrompt") },
        ];
  // The `/` menu. Unlike the openers above it stays available for the whole
  // conversation and on a tailor, because the user asks for it by typing. The
  // order is the order it is offered in: write it, sharpen it, check it, then
  // the two that spin off work of their own.
  const chatCommands: ChatCommand[] | undefined = chatUnavailable
    ? undefined
    : [
        {
          key: "fill",
          label: t("suggestFillLabel"),
          description: t("cmdFillDesc"),
          prompt: t("suggestFillPrompt"),
        },
        {
          key: "bullets",
          label: t("suggestBulletsLabel"),
          description: t("cmdBulletsDesc"),
          prompt: t("suggestBulletsPrompt"),
        },
        {
          key: "numbers",
          label: t("cmdNumbersLabel"),
          description: t("cmdNumbersDesc"),
          prompt: t("cmdNumbersPrompt"),
        },
        {
          key: "plain",
          label: t("cmdPlainLabel"),
          description: t("cmdPlainDesc"),
          prompt: t("cmdPlainPrompt"),
        },
        {
          key: "proofread",
          label: t("cmdProofreadLabel"),
          description: t("cmdProofreadDesc"),
          prompt: t("cmdProofreadPrompt"),
        },
        {
          key: "ats",
          label: t("suggestAtsLabel"),
          description: t("cmdAtsDesc"),
          prompt: t("suggestAtsPrompt"),
        },
        {
          key: "review",
          label: t("cmdReviewLabel"),
          description: t("cmdReviewDesc"),
          prompt: t("cmdReviewPrompt"),
        },
        {
          key: "onepage",
          label: t("cmdOnePageLabel"),
          description: t("cmdOnePageDesc"),
          prompt: t("cmdOnePagePrompt"),
        },
        {
          key: "jd",
          label: t("cmdJdLabel"),
          description: t("cmdJdDesc"),
          prompt: t("cmdJdPrompt"),
          needsInput: true,
        },
        {
          key: "tailor",
          label: t("cmdTailorLabel"),
          description: t("cmdTailorDesc"),
          prompt: t("cmdTailorPrompt"),
          needsInput: true,
        },
      ];
  const masterTitle =
    family && family.master.id !== resumeId ? family.master.title : title;
  const tailorCount = family && family.master.id === resumeId ? family.tailors.length : 0;

  const previewPane = (
    <EditorPreview
      previewUrls={previewUrls}
      textLayers={textLayers}
      editBands={editBands}
      deleteBands={deleteBands}
      previewAlt={t("preview")}
      previewError={t("previewError")}
      report={ats}
      downloadLabel={downloading ? t("downloading") : t("download")}
      downloading={downloading}
      onDownload={onDownload}
      onFixAts={onFixAts}
      fixingAts={sending}
      showAts={!(narrow && sheetOpen)}
      overlay={
        compileError ? (
          <CompileErrorDialog
            error={compileError}
            copyLabel={t("compileErrorCopy")}
            copiedLabel={t("compileErrorCopied")}
            askLabel={t("compileErrorAskAi")}
            asking={sending}
            onCopy={onCopyCompileError}
            onAsk={onAskCompileError}
          />
        ) : null
      }
    />
  );

  const editorPanes = (
    <Tabs
      value={activeTab}
      onValueChange={(tab) => {
        const next = normalizeEditorTab(tab);
        setLeftTab(next);
        saveEditorTab(resumeId, next);
      }}
      className="flex h-full min-h-0 min-w-0 flex-col gap-0"
    >
      <div className="editor-tab-scroll flex shrink-0 items-center bg-background">
        <TabsList className="editor-tabs h-auto w-full justify-start rounded-none bg-transparent p-0">
          <TabsTrigger value="typst" className={editorTabClassName}>
            <AlignLeft aria-hidden="true" />
            {t("tabTypst")}
          </TabsTrigger>
          <TabsTrigger value="fields" className={editorTabClassName}>
            <SlidersHorizontal aria-hidden="true" />
            {t("tabFields")}
          </TabsTrigger>
          <TabsTrigger value="chat" className={editorTabClassName}>
            <MessageCircle aria-hidden="true" />
            {t("tabChat")}
          </TabsTrigger>
          <TabsTrigger value="template" className={editorTabClassName}>
            <LayoutTemplate aria-hidden="true" />
            {t("tabTemplate")}
          </TabsTrigger>
          <TabsTrigger value="settings" className={editorTabClassName}>
            <Settings aria-hidden="true" />
            {t("tabSettings")}
          </TabsTrigger>
          <TabsTrigger value="history" className={editorTabClassName}>
            <History aria-hidden="true" />
            {t("tabHistory")}
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent
        value="typst"
        forceMount
        className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
      >
        <TypstSourceEditor
          value={source}
          onChange={setSource}
          ariaLabel={t("tabTypst")}
          lang={locale}
        />
      </TabsContent>
      <TabsContent
        value="fields"
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden data-[state=inactive]:hidden"
      >
        {/* Mounted only once the handover above has flushed and taken ownership, so
            the projection it fetches can never be of a document older than the
            buffer this mount holds. */}
        {fieldsOpen ? (
          <StructuredPanel
            resumeId={resumeId}
            source={source}
            applyEdit={editQueue.push}
            publishProjection={editQueue.publish}
            onFocusUnit={setFocusedUnit}
            onArmedUnits={setArmedUnits}
            onResyncSource={resyncSource}
          />
        ) : (
          <p className="p-3 text-sm text-muted-foreground sm:p-4">{t("fieldsLoading")}</p>
        )}
      </TabsContent>
      <TabsContent
        value="chat"
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden data-[state=inactive]:hidden"
      >
        <EditorChatPanel
          messages={messages}
          contextCutMessageId={contextCut?.fromMessageId ?? null}
          contextCutTitle={t("contextTrimmed")}
          contextCutDetail={
            contextCut ? t("contextTrimmedHint", { count: contextCut.dropped }) : undefined
          }
          contextCutJumpLabel={t("contextTrimmedJump")}
          scrollToLatestLabel={t("scrollToLatest")}
          streamingText={streamingText}
          reasoningText={reasoningText}
          turnProgressed={turnProgressed}
          workingLabel={t("working")}
          noReasoningLabel={t("noReasoning")}
          thinkingLabel={t("thinking")}
          draft={draft}
          onDraftChange={onDraftChange}
          attachment={attachment}
          onAttachmentChange={setAttachment}
          onSubmit={onChat}
          sending={sending}
          placeholder={t("composerPlaceholder")}
          composerBlockedReason={
            importRunning
              ? t("importComposerBusy")
              : tailorRunning
                ? t("tailorRunComposerBusy")
                : null
          }
          sendLabel={sending ? t("sending") : t("send")}
          emptyHint={chatEmptyHint}
          ariaLabel={t("tabChat")}
          attachLabel={t("attach")}
          attachRemoveLabel={t("attachRemove")}
          attachHint={t("attachHint")}
          attachBadType={t("attachBadType")}
          attachTooLarge={t("attachTooLarge")}
          currentSource={source}
          onRestoreEdit={onRestoreEdit}
          familyActions={familyActions}
          tailorActions={tailorActions}
          suggestions={chatSuggestions}
          commands={chatCommands}
          commandsTitle={t("cmdMenuTitle")}
          commandsNeedsInput={t("cmdNeedsInput")}
          commandsHint={t("cmdHint")}
          focusKey={chatFocusKey}
          usage={usagePanel}
          models={modelPanel}
          tailorRunView={tailorRunView}
          tailorRunActionPending={tailorActionPending}
          onTailorRunRetry={startTailor}
          importView={importView}
          importElapsedSeconds={importElapsedSeconds}
          importCardIndex={importCardAt}
          importActionPending={importActionPending}
          onImportRetry={onImportAction}
          onImportResume={onImportAction}
        />
      </TabsContent>
      <TabsContent
        value="template"
        className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
      >
        <EditorTemplatePanel
          sending={sending || templateApplyPending}
          onApply={onApplyTemplate}
        />
      </TabsContent>
      <TabsContent
        value="settings"
        className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
      >
        <EditorSettingsPanel
          resumeId={resumeId}
          title={title}
          createdAt={createdAt}
          source={resumeSource}
          resumeLocale={resumeLocale}
          importStatus={effectiveImportStatus}
          onTitleChange={setTitle}
          onShareState={onShareState}
          unpublishedChanges={unpublishedChanges}
          publishing={publishing}
          onPublish={signedIn ? onPublish : undefined}
          kind={kind}
          tailorCount={tailorCount}
          deleteDisabled={sending}
          onRequestDelete={openDeleteDialog}
        />
      </TabsContent>
      <TabsContent
        value="history"
        forceMount
        className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
      >
        <EditorHistoryPanel
          resumeId={resumeId}
          reloadKey={`${activeTab}:${updatedAt}`}
          onRestored={onRestoreHistory}
        />
      </TabsContent>
    </Tabs>
  );

  // h-dvh, not h-screen: on a phone the URL-bar-inclusive viewport would push
  // the composer and the ATS strip under the browser chrome.
  return (
    <div
      className="flex h-dvh flex-col overflow-hidden bg-background"
      style={visibleHeight ? { height: visibleHeight } : undefined}
    >
      <EditorHeader
        back={
          <button
            type="button"
            onClick={() => void onLeaveEditor()}
            disabled={leaving}
            aria-label={backLabel}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-1 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-60"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
            {/* Where "back" goes depends on who this is, and that answer lands
                a tick after the header does. Holding the label's width while
                it is unknown keeps the title from sliding, and rendering it
                invisible rather than wrong means nobody reads a destination
                that is about to change. */}
            <span className={cn("hidden sm:inline", me === undefined && "invisible")}>
              {backLabel}
            </span>
          </button>
        }
        title={title}
        familyMenu={
          <ResumeFamilyMenu
            family={familyView}
            currentId={resumeId}
            title={title}
            disabled={sending}
            onSelect={(id) => void onSwitchResume(id)}
            onNewTailor={openTailorDialog}
            labels={{
              menu: t("familyMenu"),
              master: t("familyMaster"),
              tailor: t("familyTailor"),
              newTailor: t("familyNewTailor"),
              newTailorHint: t("familyNewTailorHint"),
              blocked: t("switchBlocked"),
            }}
          />
        }
        actions={
          <MemoryPanel
            items={memory}
            notice={memoryNotice}
            actions={familyActions}
            onDeleteMemory={(id) => void runMemoryAction(id, deleteMemory)}
          />
        }
        accountMenu={
          <AccountMenu
            user={me}
            onLoggedOut={() => {
              setMe(null);
              router.push("/");
            }}
          />
        }
        status={status}
        publishLabel={isPublic ? t("publish") : t("publishSave")}
        publishingLabel={isPublic ? t("publishing") : t("publishSaving")}
        unpublishedChanges={signedIn && unpublishedChanges}
        publishing={publishing}
        onPublish={signedIn ? onPublish : undefined}
      />
      <div className="min-h-0 min-w-0 flex-1">
        {narrow ? (
          <EditorMobileLayout
            preview={previewPane}
            panel={editorPanes}
            open={sheetOpen}
            onOpenChange={setSheetOpen}
            onAskAi={askAi}
            labels={{
              ask: t("mobileAsk"),
              tools: t("mobileTools"),
              close: t("mobileClose"),
              expand: t("mobileExpand"),
              collapse: t("mobileCollapse"),
              resize: t("mobileResize"),
            }}
          />
        ) : (
          <ResizablePanelGroup direction="horizontal" className="h-full">
            <ResizablePanel defaultSize={48} minSize={28} maxSize={65} className="h-full min-w-0">
              <div className="relative h-full min-h-0 min-w-0">{editorPanes}</div>
            </ResizablePanel>
            <ResizableHandle withHandle className="bg-rule" />
            <ResizablePanel defaultSize={52} minSize={35} className="h-full">
              {previewPane}
            </ResizablePanel>
          </ResizablePanelGroup>
        )}
      </div>
      {tailorDialogOpen ? (
        <CreateTailorDialog
          defaultTitle={`${masterTitle} – `}
          canForkCurrent={kind === "tailor"}
          creating={creatingTailor}
          error={tailorError}
          onCreate={(payload) => void onCreateTailor(payload)}
          onClose={() => setTailorDialogOpen(false)}
          labels={{
            title: t("tailorDialogTitle"),
            hint: t("tailorDialogHint"),
            name: t("tailorTitle"),
            targetLabel: t("tailorTargetLabel"),
            targetContext: t("tailorTargetContext"),
            create: t("tailorCreate"),
            creating: t("tailorCreating"),
            cancel: t("tailorCancel"),
            forkCurrent: t("tailorForkCurrent"),
          }}
        />
      ) : null}
      {deleteDialogOpen ? (
        <ConfirmDeleteDialog
          title={tCommon("deleteTitle", { title })}
          description={
            kind === "tailor"
              ? tCommon("deleteTailorHint")
              : tailorCount > 0
                ? tCommon("deleteMasterWithTailorsHint", { count: tailorCount })
                : tCommon("deleteMasterHint")
          }
          busy={deletingResume}
          error={deleteError}
          onConfirm={() => void onConfirmDelete()}
          onClose={() => setDeleteDialogOpen(false)}
          labels={{
            confirm: tCommon("deleteConfirm"),
            confirming: tCommon("deleting"),
            cancel: tCommon("deleteCancel"),
          }}
        />
      ) : null}
    </div>
  );
}
