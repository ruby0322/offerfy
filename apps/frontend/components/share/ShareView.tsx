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

export default function ShareView({ token }: Props) {
  const t = useTranslations("share");
  const [title, setTitle] = useState("");
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState(false);
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
      try {
        const meta = await getPublicShare(token);
        if (cancelled) return;
        setTitle(meta.title);
        const pages = await getPublicPreviewPages(token);
        if (!cancelled) setPreviewPages(pages);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setMissing(true);
          return;
        }
        setLoadError(true);
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

  if (missing) {
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
      <header className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-3">
        <Link href="/" className="shrink-0 text-lg font-bold tracking-tight">
          {t("brand")}
        </Link>
        <span className="truncate text-sm text-muted-foreground">{title}</span>
      </header>
      <div className="min-h-0 flex-1">
        <EditorPreview
          previewUrls={previewUrls}
          previewAlt={t("preview")}
          previewError={loadError ? t("previewError") : ""}
          report={null}
          showAts={false}
          downloadLabel={downloading ? t("downloading") : t("download")}
          downloading={downloading}
          onDownload={onDownload}
        />
      </div>
    </div>
  );
}
