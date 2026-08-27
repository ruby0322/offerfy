"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useLocale, useTranslations } from "next-intl";
import CompileErrorDialog from "@/components/editor/CompileErrorDialog";
import EditorChatPanel from "@/components/editor/EditorChatPanel";
import EditorHeader from "@/components/editor/EditorHeader";
import EditorPreview from "@/components/editor/EditorPreview";
import EditorSettingsPanel from "@/components/editor/EditorSettingsPanel";
import EditorTemplatePanel from "@/components/editor/EditorTemplatePanel";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  claimResumes,
  compileResume,
  exportPdf,
  getAtsReport,
  getChatMessages,
  getMe,
  getPreviewPages,
  getResume,
  putResumeSource,
  sendChat,
  typstCompileDetail,
  type AtsReport,
  type ChatMessage,
  type ImportStatus,
  type ResumeSource,
} from "@/lib/api";
import { formatUserAttachmentMessage } from "@/lib/chat-tools";
import type { AtsCheckName } from "@/lib/ats-checks";
import { peekPendingUpload, takePendingUpload } from "@/lib/pending-upload";

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

type Props = {
  resumeId: string;
};

export default function EditorShell({ resumeId }: Props) {
  const t = useTranslations("editor");
  const tAts = useTranslations("ats");
  const tCommon = useTranslations("common");
  const tNav = useTranslations("nav");
  const locale = useLocale();
  const [title, setTitle] = useState("");
  const [createdAt, setCreatedAt] = useState<string | undefined>();
  const [resumeSource, setResumeSource] = useState<ResumeSource | undefined>();
  const [resumeLocale, setResumeLocale] = useState<string | undefined>();
  const [importStatus, setImportStatus] = useState<ImportStatus | undefined>();
  const [source, setSource] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [ats, setAts] = useState<AtsReport | null>(null);
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [chatUnavailable, setChatUnavailable] = useState(false);
  const [leftTab, setLeftTab] = useState(() =>
    peekPendingUpload(resumeId) ? "chat" : "typst",
  );
  const [compileError, setCompileError] = useState<string | null>(null);
  const skipTimers = useRef(true);
  const sendingRef = useRef(false);
  const previewObjectUrls = useRef<string[]>([]);

  const setPreviewPages = useCallback((svgs: string[]) => {
    for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    const urls = svgs.map((svg) =>
      URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })),
    );
    previewObjectUrls.current = urls;
    setPreviewUrls(urls);
  }, []);

  const refreshPreview = useCallback(async () => {
    const pages = await getPreviewPages(resumeId);
    setPreviewPages(pages);
    setCompileError(null);
  }, [resumeId, setPreviewPages]);

  const refreshAts = useCallback(async () => {
    const report = await getAtsReport(resumeId);
    setAts(report);
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        try {
          const me = await getMe();
          if (me) {
            try {
              await claimResumes();
            } catch {
              /* claim is best-effort */
            }
          }
        } catch {
          /* auth check is optional */
        }
        const resume = await getResume(resumeId);
        if (cancelled) return;
        setTitle(resume.title || "");
        setCreatedAt(resume.created_at);
        setResumeSource(resume.source);
        setResumeLocale(resume.locale);
        setImportStatus(resume.import_status);
        setSource(resume.typst_source || "");
        try {
          const history = await getChatMessages(resumeId);
          if (!cancelled) setMessages(history.map(withMessageId));
        } catch {
          /* chat history is optional */
        }
        if (cancelled) return;
        setLoaded(true);
        try {
          const pages = await getPreviewPages(resumeId);
          if (!cancelled) {
            setPreviewPages(pages);
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
  }, [failCompile, resumeId, setPreviewPages, t]);

  useEffect(() => {
    return () => {
      for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    };
  }, []);

  useEffect(() => {
    if (!loaded) return;
    if (skipTimers.current) return;
    const handle = window.setTimeout(async () => {
      try {
        setStatus(t("saving"));
        await putResumeSource(resumeId, { typst_source: source });
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
  }, [failCompile, loaded, refreshAts, refreshPreview, resumeId, source, t]);

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
      options?: { preferFullSource?: boolean },
    ) => {
    const trimmed = text.trim();
    if ((!trimmed && !file) || sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    setMessages((prev) => [
      ...prev,
      withMessageId({
        role: "user",
        content: formatUserAttachmentMessage(trimmed, file?.name ?? null),
        timestamp: new Date().toISOString(),
      }),
    ]);
    try {
      skipTimers.current = true;
      await putResumeSource(resumeId, { typst_source: source });
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
        if (event.type === "tool" || event.type === "assistant") {
          setChatUnavailable(false);
          setMessages((prev) => [...prev, withMessageId(event.message)]);
          return;
        }
        if (event.type === "source") {
          skipTimers.current = true;
          setSource(event.typst_source);
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
          return;
        }
        if (event.type === "error") {
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
      if (err instanceof ApiError && err.status === 503) {
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
    }
    },
    [failCompile, refreshAts, refreshPreview, resumeId, source, t],
  );
  const sendUserChatRef = useRef(sendUserChat);
  useEffect(() => {
    sendUserChatRef.current = sendUserChat;
  }, [sendUserChat]);

  useEffect(() => {
    if (!loaded) return;
    if (!peekPendingUpload(resumeId)) return;
    const prompt = t("importFillPrompt");
    const handle = window.setTimeout(() => {
      const file = takePendingUpload(resumeId);
      if (!file) return;
      void sendUserChatRef.current(prompt, file, { preferFullSource: true });
    }, 0);
    return () => window.clearTimeout(handle);
  }, [loaded, resumeId, t]);

  async function onChat(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    const file = attachment;
    if ((!text && !file) || sendingRef.current) return;
    setDraft("");
    setAttachment(null);
    await sendUserChat(text, file);
  }

  function onApplyTemplate(prompt: string) {
    setLeftTab("chat");
    void sendUserChat(prompt, null, { preferFullSource: true });
  }

  async function onRestoreEdit(previousSource: string) {
    if (sendingRef.current || previousSource === source) return;
    sendingRef.current = true;
    setSending(true);
    skipTimers.current = true;
    setSource(previousSource);
    try {
      await putResumeSource(resumeId, { typst_source: previousSource });
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

  const chatEmptyHint = chatUnavailable ? t("chatNotConfigured") : t("chatEmpty");

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <EditorHeader brand={tNav("brand")} title={title} status={status} />
      <div className="min-h-0 flex-1">
        <ResizablePanelGroup direction="horizontal" className="h-full">
          <ResizablePanel defaultSize={48} minSize={28} maxSize={65} className="h-full">
            <div className="relative h-full min-h-0">
            <Tabs
              value={leftTab}
              onValueChange={setLeftTab}
              className="flex h-full min-h-0 flex-col gap-0"
            >
              <div className="flex shrink-0 items-center overflow-x-auto border-b border-border bg-background px-2 py-2 sm:px-3">
                <TabsList className="shrink-0">
                  <TabsTrigger value="typst" className="px-1.5 text-xs sm:px-2 sm:text-sm">
                    {t("tabTypst")}
                  </TabsTrigger>
                  <TabsTrigger value="chat" className="px-1.5 text-xs sm:px-2 sm:text-sm">
                    {t("tabChat")}
                  </TabsTrigger>
                  <TabsTrigger value="template" className="px-1.5 text-xs sm:px-2 sm:text-sm">
                    {t("tabTemplate")}
                  </TabsTrigger>
                  <TabsTrigger value="settings" className="px-1.5 text-xs sm:px-2 sm:text-sm">
                    {t("tabSettings")}
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
                value="chat"
                className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
              >
                <EditorChatPanel
                  messages={messages}
                  draft={draft}
                  onDraftChange={setDraft}
                  attachment={attachment}
                  onAttachmentChange={setAttachment}
                  onSubmit={onChat}
                  sending={sending}
                  placeholder={t("composerPlaceholder")}
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
                />
              </TabsContent>
              <TabsContent
                value="template"
                className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
              >
                <EditorTemplatePanel sending={sending} onApply={onApplyTemplate} />
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
                  importStatus={importStatus}
                  onTitleChange={setTitle}
                />
              </TabsContent>
            </Tabs>
            </div>
          </ResizablePanel>
          <ResizableHandle withHandle className="bg-rule" />
          <ResizablePanel defaultSize={52} minSize={35} className="h-full">
            <EditorPreview
              previewUrls={previewUrls}
              previewAlt={t("preview")}
              previewError={t("previewError")}
              report={ats}
              downloadLabel={downloading ? t("downloading") : t("download")}
              downloading={downloading}
              onDownload={onDownload}
              onFixAts={onFixAts}
              fixingAts={sending}
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
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  );
}
