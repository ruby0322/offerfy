"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useLocale, useTranslations } from "next-intl";
import EditorChatPanel from "@/components/editor/EditorChatPanel";
import EditorHeader from "@/components/editor/EditorHeader";
import EditorPreview from "@/components/editor/EditorPreview";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  compileResume,
  exportPdf,
  getAtsReport,
  getChatMessages,
  getPreviewPages,
  getResume,
  isImportPending,
  putResumeSource,
  sendChat,
  type AtsReport,
  type ChatMessage,
  type ImportStatus,
} from "@/lib/api";
import { chatAppliedTypstEdit } from "@/lib/chat-tools";

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
  const tCommon = useTranslations("common");
  const tNav = useTranslations("nav");
  const locale = useLocale();
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [ats, setAts] = useState<AtsReport | null>(null);
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [importStatus, setImportStatus] = useState<ImportStatus>("idle");
  const [chatUnavailable, setChatUnavailable] = useState(false);
  const skipTimers = useRef(true);
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
  }, [resumeId, setPreviewPages]);

  const refreshAts = useCallback(async () => {
    const report = await getAtsReport(resumeId);
    setAts(report);
  }, [resumeId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resume = await getResume(resumeId);
        if (cancelled) return;
        setTitle(resume.title || "");
        setSource(resume.typst_source || "");
        setImportStatus(resume.import_status ?? "idle");
        setLoaded(true);
        try {
          const pages = await getPreviewPages(resumeId);
          if (!cancelled) setPreviewPages(pages);
        } catch {
          if (!cancelled) setStatus(t("previewError"));
        }
        try {
          const report = await getAtsReport(resumeId);
          if (!cancelled) setAts(report);
        } catch {
          /* ATS appears after a successful compile */
        }
        try {
          const history = await getChatMessages(resumeId);
          if (!cancelled) setMessages(history.map(withMessageId));
        } catch {
          /* chat history is optional */
        }
      } catch {
        if (!cancelled) setLoadError(t("loadError"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [resumeId, setPreviewPages, t]);

  useEffect(() => {
    return () => {
      for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    };
  }, []);

  useEffect(() => {
    if (!isImportPending(importStatus)) return;
    let cancelled = false;

    async function applySettledImport() {
      const resume = await getResume(resumeId);
      if (cancelled) return;
      const nextStatus = resume.import_status ?? "idle";
      if (isImportPending(nextStatus)) return;
      skipTimers.current = true;
      setTitle(resume.title || "");
      setSource(resume.typst_source || "");
      setImportStatus(nextStatus);
      try {
        await refreshPreview();
      } catch {
        setStatus(t("previewError"));
      }
      try {
        await refreshAts();
      } catch {
        /* ATS needs a successful PDF compile */
      }
      try {
        setMessages((await getChatMessages(resumeId)).map(withMessageId));
      } catch {
        /* chat history is optional */
      }
      skipTimers.current = false;
    }

    const handle = window.setInterval(() => {
      void applySettledImport();
    }, 1500);
    void applySettledImport();
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [importStatus, refreshAts, refreshPreview, resumeId, t]);

  useEffect(() => {
    if (!loaded) return;
    if (isImportPending(importStatus)) return;
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
      } catch {
        setStatus(t("previewError"));
      }
    }, 400);
    return () => window.clearTimeout(handle);
  }, [importStatus, loaded, refreshAts, refreshPreview, resumeId, source, t]);

  useEffect(() => {
    if (!loaded) return;
    if (isImportPending(importStatus)) return;
    if (skipTimers.current) {
      skipTimers.current = false;
      return;
    }
    const handle = window.setTimeout(async () => {
      try {
        await compileResume(resumeId, "pdf");
        await refreshAts();
      } catch {
        setStatus(t("compileError"));
      }
    }, 2000);
    return () => window.clearTimeout(handle);
  }, [importStatus, loaded, refreshAts, resumeId, source, t]);

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
    } catch {
      setStatus(t("compileError"));
    } finally {
      setDownloading(false);
    }
  }

  async function onChat(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    setMessages((prev) => [
      ...prev,
      withMessageId({ role: "user", content: text, timestamp: new Date().toISOString() }),
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
      setSending(false);
      return;
    } finally {
      skipTimers.current = false;
    }
    try {
      const result = await sendChat(resumeId, text);
      setChatUnavailable(false);
      const reply = result.reply || t("chatError");
      setMessages((prev) => {
        if (result.messages.length > 0) {
          return result.messages.map((msg, index) =>
            withMessageId({ ...msg, id: prev[index]?.id ?? msg.id }),
          );
        }
        return [
          ...prev,
          withMessageId({ role: "assistant", content: reply, timestamp: new Date().toISOString() }),
        ];
      });
      if (
        result.typstSource &&
        chatAppliedTypstEdit(result.messages, result.typstSource, source, result.applied)
      ) {
        skipTimers.current = true;
        setSource(result.typstSource);
        try {
          await putResumeSource(resumeId, { typst_source: result.typstSource });
          await refreshPreview();
          await compileResume(resumeId, "pdf");
          await refreshAts();
        } catch {
          setStatus(t("previewError"));
        } finally {
          skipTimers.current = false;
        }
      }
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
      setSending(false);
    }
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white p-6 dark:bg-gray-950 dark:text-gray-100">
        <p>{loadError}</p>
      </div>
    );
  }

  if (!loaded) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white p-6 dark:bg-gray-950">
        <p className="text-sm text-gray-600 dark:text-gray-400">{tCommon("loading")}</p>
      </div>
    );
  }

  const headerStatus = isImportPending(importStatus) ? t("importing") : status;
  const chatEmptyHint = chatUnavailable
    ? t("chatNotConfigured")
    : isImportPending(importStatus)
      ? t("importing")
      : t("chatEmpty");

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-white dark:bg-gray-950">
      <EditorHeader brand={tNav("brand")} title={title} status={headerStatus} />
      <div className="min-h-0 flex-1">
        <ResizablePanelGroup direction="horizontal" className="h-full">
          <ResizablePanel defaultSize={48} minSize={28} maxSize={65} className="h-full">
            <Tabs defaultValue="typst" className="flex h-full min-h-0 flex-col gap-0">
              <div className="flex shrink-0 items-center border-b border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                <TabsList>
                  <TabsTrigger value="typst">{t("tabTypst")}</TabsTrigger>
                  <TabsTrigger value="chat">{t("tabChat")}</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent
                value="typst"
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
                  onSubmit={onChat}
                  sending={sending}
                  placeholder={t("composerPlaceholder")}
                  sendLabel={sending ? t("sending") : t("send")}
                  emptyHint={chatEmptyHint}
                  ariaLabel={t("tabChat")}
                />
              </TabsContent>
            </Tabs>
          </ResizablePanel>
          <ResizableHandle withHandle className="bg-gray-200 dark:bg-gray-700" />
          <ResizablePanel defaultSize={52} minSize={35} className="h-full">
            <EditorPreview
              previewUrls={previewUrls}
              previewAlt={t("preview")}
              previewError={t("previewError")}
              report={ats}
              downloadLabel={downloading ? t("downloading") : t("download")}
              downloading={downloading}
              onDownload={onDownload}
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  );
}
