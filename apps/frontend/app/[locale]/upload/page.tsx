"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Dropzone from "@/components/upload/Dropzone";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, startImport, uploadResume } from "@/lib/api";
import { saveEditorTab } from "@/lib/editor-tab";
import { synthesizePastedTextFile, validatePastedText } from "@/lib/upload-intake";

const CHIP_KEYS = ["chipPdf", "chipPhoto", "chipTxt", "chipMd", "chipMaxSize"] as const;

/** True while focus sits in something that owns its own paste behaviour.
 *  There is no such field on this page today, but the paste listener is
 *  global (it has to be, to catch a paste anywhere on the page) and must
 *  not fight a field that lands here later. */
function focusIsEditable(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return true;
  return el instanceof HTMLElement && el.isContentEditable;
}

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

  // A global paste door: a copied PDF, a screenshot, or a wall of text from
  // a doc, with nowhere else on the page to drop it. Validation itself lives
  // in lib/upload-intake.ts so Dropzone's drag/click path and this listener
  // can't drift apart.
  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      if (busy) return;
      if (focusIsEditable()) return;
      const clipboardData = event.clipboardData;
      if (!clipboardData) return;

      const pastedFile = clipboardData.files[0];
      if (pastedFile) {
        event.preventDefault();
        setError(null);
        void onFile(pastedFile);
        return;
      }

      const text = clipboardData.getData("text/plain");
      if (!text) return;
      event.preventDefault();
      const result = validatePastedText(text);
      if (!result.ok) {
        setError(t(result.reason));
        return;
      }
      setError(null);
      void onFile(synthesizePastedTextFile(text));
    }

    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
    // onFile closes over state that is already current at call time
    // (resumeId, navigation) and does not need to retrigger this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, t]);

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
          <div className="rr-chip-row mt-5">
            {CHIP_KEYS.map((key) => (
              <span key={key} className="rr-chip">
                {t(key)}
              </span>
            ))}
          </div>
          <p className="mt-4 text-xs text-muted-foreground">{t("privacy")}</p>
          {busy ? <p className="mt-4 text-sm text-muted-foreground">{t("uploading")}</p> : null}
          {error ? <p className="mt-4 text-sm text-destructive">{error}</p> : null}
          <p className="mt-6 text-center">
            <Link href="/create" className="rr-back-link text-sm">
              {t("blankTemplate")}
            </Link>
          </p>
        </div>
      </main>
      <Footer variant="app" />
    </div>
  );
}
