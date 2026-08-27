"use client";

import ChatMessageCard from "@/components/editor/ChatMessageCard";
import AtsStrip from "@/components/editor/AtsStrip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  adminResume,
  adminResumeAts,
  adminResumeMessages,
  adminResumePreview,
  typstCompileDetail,
  type AdminChatMessage,
  type AdminResumeDetail,
  type AtsReport,
  type ChatMessage,
} from "@/lib/api";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import dynamic from "next/dynamic";
import { use, useEffect, useState } from "react";

const TypstSourceEditor = dynamic(
  () => import("@/components/editor/TypstSourceEditor"),
  { ssr: false },
);

function stamp(value: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

function svgDataUrl(svg: string) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function asChat(message: AdminChatMessage): ChatMessage {
  const role = message.role as ChatMessage["role"];
  return {
    id: message.id,
    role,
    content: message.content,
    timestamp: message.created_at,
  };
}

export default function AdminResumeInspectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const t = useTranslations("admin");
  const [resume, setResume] = useState<AdminResumeDetail | null>(null);
  const [error, setError] = useState(false);
  const [tab, setTab] = useState("source");
  const [messages, setMessages] = useState<AdminChatMessage[] | null>(null);
  const [previewUrls, setPreviewUrls] = useState<string[] | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [ats, setAts] = useState<AtsReport | null>(null);
  const [atsError, setAtsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminResume(id)
      .then((next) => {
        if (!cancelled) setResume(next);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (tab !== "chat" || messages) return;
    let cancelled = false;
    adminResumeMessages(id)
      .then((items) => {
        if (!cancelled) setMessages(items);
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id, tab, messages]);

  useEffect(() => {
    if (tab !== "preview" || previewUrls || previewError) return;
    let cancelled = false;
    adminResumePreview(id)
      .then((body) => {
        if (!cancelled) setPreviewUrls(body.pages.map(svgDataUrl));
      })
      .catch((err) => {
        if (!cancelled) {
          setPreviewError(
            typstCompileDetail(err) || (err instanceof ApiError ? err.message : t("error")),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, tab, previewUrls, previewError, t]);

  useEffect(() => {
    if (tab !== "ats" || ats || atsError) return;
    let cancelled = false;
    adminResumeAts(id)
      .then((report) => {
        if (!cancelled) setAts(report);
      })
      .catch((err) => {
        if (!cancelled) {
          setAtsError(
            typstCompileDetail(err) || (err instanceof ApiError ? err.message : t("error")),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, tab, ats, atsError, t]);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!resume) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  return (
    <div className="flex min-h-[70vh] flex-col gap-4">
      <header className="sticky top-0 z-10 space-y-1 border-b border-border bg-paper pb-4">
        <h1 className="text-2xl font-semibold">{resume.title}</h1>
        <p className="text-sm text-muted-foreground">
          {t("owner")}{" "}
          {resume.owner_kind === "user" ? (
            <Link href={`/admin/users/${resume.owner_id}`} className="underline">
              {resume.owner_label}
            </Link>
          ) : (
            resume.owner_label
          )}
          {" · "}
          {t("source")} {resume.source}
          {" · "}
          {t("importStatus")} {resume.import_status}
          {" · "}
          {t("claimed")} {stamp(resume.claimed_at)}
          {" · "}
          {t("created")} {stamp(resume.created_at)}
          {" · "}
          {t("messages")} {resume.message_count}
        </p>
      </header>
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList>
          <TabsTrigger value="source">{t("tabSource")}</TabsTrigger>
          <TabsTrigger value="chat">{t("tabChat")}</TabsTrigger>
          <TabsTrigger value="preview">{t("tabPreview")}</TabsTrigger>
          <TabsTrigger value="ats">{t("tabAts")}</TabsTrigger>
        </TabsList>
        <TabsContent value="source" className="flex h-[28rem] min-h-[28rem] flex-col overflow-hidden rounded-md border border-border">
          <TypstSourceEditor
            value={resume.typst_source}
            readOnly
            ariaLabel={t("tabSource")}
            lang={resume.locale}
          />
        </TabsContent>
        <TabsContent value="chat" className="space-y-3">
          {messages == null ? (
            <p className="text-sm text-muted-foreground">{t("loading")}</p>
          ) : messages.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noChat")}</p>
          ) : (
            messages.map((message) => (
              <ChatMessageCard key={message.id} message={asChat(message)} />
            ))
          )}
        </TabsContent>
        <TabsContent value="preview">
          {previewError ? (
            <p className="text-sm text-destructive">{previewError}</p>
          ) : previewUrls == null ? (
            <p className="text-sm text-muted-foreground">{t("loading")}</p>
          ) : previewUrls.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("previewPending")}</p>
          ) : (
            <div className="flex flex-col gap-4">
              {previewUrls.map((url, index) => (
                <div key={url} className="overflow-hidden rounded-lg border border-border bg-sheet">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={url}
                    alt={`${resume.title} ${index + 1}`}
                    className="block h-auto w-[min(100%,52rem)]"
                  />
                </div>
              ))}
            </div>
          )}
        </TabsContent>
        <TabsContent value="ats">
          {atsError ? (
            <p className="text-sm text-destructive">{atsError}</p>
          ) : ats == null ? (
            <p className="text-sm text-muted-foreground">{t("loading")}</p>
          ) : (
            <AtsStrip report={ats} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
