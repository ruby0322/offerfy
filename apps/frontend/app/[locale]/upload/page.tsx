"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Dropzone from "@/components/upload/Dropzone";
import { useRouter } from "@/i18n/navigation";
import { ApiError, startImport, uploadResume } from "@/lib/api";
import { saveEditorTab } from "@/lib/editor-tab";

export default function UploadPage() {
  const t = useTranslations("upload");
  const locale = useLocale();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File) {
    setBusy(true);
    setError(null);

    // Two different outcomes, deliberately not one catch. A failed upload
    // leaves nothing behind and belongs on this page. A failed *import start*
    // does not: the resume row and its file both exist by then, and staying
    // here leaves the person with no link to what they just uploaded, so they
    // upload it again and collect duplicates.
    let resumeId: string;
    try {
      const resume = await uploadResume({ file, locale });
      resumeId = resume.id;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("error"));
      setBusy(false);
      return;
    }

    try {
      // The import is server-owned from here: nothing about the file itself
      // needs to survive in the browser, so a reload between this call and
      // the first chat turn costs nothing.
      await startImport(resumeId);
    } catch {
      // Navigate anyway. That is what the server-owned design buys: the
      // editor reads the run's own state and offers Retry, so the way out of
      // a failed start is in the editor rather than on this page. The
      // backend's own words are not shown -- a 409 ("an import is already
      // running for resume ...") is not something to put in front of anyone,
      // and it is not even a problem: the editor attaches to that run.
    }

    // Write the editor's remembered tab *before* navigating, the same key
    // EditorShell reads synchronously in its useState initialiser. That makes
    // the editor mount straight onto chat, where the progress card lives,
    // with no flash of the Typst tab first. A storage failure here (private
    // mode, quota) is cosmetic -- the editor just opens on whatever tab it
    // would otherwise default to -- so it must not block the navigation.
    saveEditorTab(resumeId, "chat");
    router.push(`/editor/${resumeId}`);
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
