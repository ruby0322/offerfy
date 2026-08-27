"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import EditorPreview from "@/components/editor/EditorPreview";
import { Link } from "@/i18n/navigation";
import {
  ApiError,
  exportPublicPdf,
  getPublicPreviewPages,
  getPublicShare,
} from "@/lib/api";

type Props = {
  token: string;
};

type LoadStatus = "loading" | "ok" | "missing" | "error";

export default function ShareView({ token }: Props) {
  const t = useTranslations("share");
  const [title, setTitle] = useState("");
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [previewError, setPreviewError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const previewObjectUrls = useRef<string[]>([]);

  const setPreviewPages = useCallback((svgs: string[]) => {
    for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    const urls = svgs.map((svg) =>
      URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })),
    );
    previewObjectUrls.current = urls;
    setPreviewUrls(urls);
  }, []);

  useEffect(() => {
    return () => {
      for (const url of previewObjectUrls.current) URL.revokeObjectURL(url);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setStatus("loading");
      setPreviewError(false);
      setTitle("");
      setPreviewPages([]);
      try {
        const meta = await getPublicShare(token);
        if (cancelled) return;
        setTitle(meta.title);
        setStatus("ok");
        try {
          const pages = await getPublicPreviewPages(token);
          if (!cancelled) setPreviewPages(pages);
        } catch {
          if (!cancelled) setPreviewError(true);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setStatus("missing");
          return;
        }
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setPreviewPages, token]);

  async function onDownload() {
    setDownloading(true);
    try {
      const blob = await exportPublicPdf(token);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${title || "resume"}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  if (status === "missing") {
    return (
      <div className="flex min-h-screen flex-col bg-background">
        <header className="border-b border-border px-4 py-3">
          <Link href="/" className="text-lg font-bold tracking-tight">
            {t("brand")}
          </Link>
        </header>
        <p className="px-4 py-12 text-sm text-muted-foreground">{t("notFound")}</p>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <header className="border-b border-border px-4 py-3">
        <Link href="/" className="text-lg font-bold tracking-tight">
          {t("brand")}
        </Link>
      </header>
      <div className="min-h-0 flex-1">
        <EditorPreview
          previewUrls={previewUrls}
          previewAlt={t("preview")}
          previewError={
            status === "error" || previewError ? t("previewError") : ""
          }
          report={null}
          showAts={false}
          downloadLabel={downloading ? t("downloading") : t("download")}
          downloading={downloading}
          onDownload={onDownload}
        />
      </div>
      {status === "ok" && (
        <p className="shrink-0 border-t border-border px-4 py-3 text-center text-sm text-muted-foreground">
          {t("metaDescription")}{" "}
          <Link href="/create" className="font-medium text-foreground underline underline-offset-2">
            {t("cta")}
          </Link>
        </p>
      )}
    </div>
  );
}
