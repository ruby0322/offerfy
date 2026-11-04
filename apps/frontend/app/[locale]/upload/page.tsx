"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Dropzone from "@/components/upload/Dropzone";
import { useRouter } from "@/i18n/navigation";
import { ApiError, startImport, uploadResume } from "@/lib/api";

export default function UploadPage() {
  const t = useTranslations("upload");
  const locale = useLocale();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File) {
    setBusy(true);
    setError(null);
    try {
      const resume = await uploadResume({ file, locale });
      // The import is server-owned from here: nothing about the file itself
      // needs to survive in the browser, so a reload between this call and
      // the first chat turn costs nothing. Only navigate once it is
      // actually running -- an editor with no import to show progress for
      // is the exact "looks stuck" failure this replaces.
      await startImport(resume.id);
      router.push(`/editor/${resume.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("error"));
      setBusy(false);
    }
  }

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-2xl px-5 py-16">
        <div className="rr-card p-8 sm:p-10">
          <h1 className="rr-page-title">{t("title")}</h1>
          <p className="rr-page-lead">{t("lead")}</p>
          <div className="mt-8">
            <Dropzone disabled={busy} onFile={onFile} />
          </div>
          {busy ? <p className="mt-4 text-sm text-muted-foreground">{t("uploading")}</p> : null}
          {error ? <p className="mt-4 text-sm text-destructive">{error}</p> : null}
        </div>
      </main>
      <Footer variant="app" />
    </div>
  );
}
