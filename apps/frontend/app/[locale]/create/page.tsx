"use client";

import { FormEvent, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { useRouter } from "@/i18n/navigation";
import { ApiError, createResume } from "@/lib/api";

export default function CreatePage() {
  const t = useTranslations("create");
  const locale = useLocale();
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const resume = await createResume({
        title: title.trim() || undefined,
        locale,
      });
      router.push(`/editor/${resume.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("error"));
      setBusy(false);
    }
  }

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-lg px-5 py-16">
        <div className="rr-card p-8 sm:p-10">
          <h1 className="rr-page-title">{t("title")}</h1>
          <p className="rr-page-lead">{t("lead")}</p>
          <form className="mt-8 space-y-5" onSubmit={onSubmit}>
            <label className="block text-sm font-medium">
              {t("nameLabel")}
              <input
                className="rr-input mt-2"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={t("namePlaceholder")}
                autoFocus
              />
            </label>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <button className="rr-btn w-full py-3" type="submit" disabled={busy}>
              {busy ? t("submitting") : t("submit")}
            </button>
          </form>
        </div>
      </main>
      <Footer variant="app" />
    </div>
  );
}
