"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Dropzone from "@/components/upload/Dropzone";
import { useRouter } from "@/i18n/navigation";
import { ApiError, uploadResume } from "@/lib/api";
import { stashPendingUpload } from "@/lib/pending-upload";

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
      stashPendingUpload(resume.id, file);
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
