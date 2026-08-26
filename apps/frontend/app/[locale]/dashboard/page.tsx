"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { Link, useRouter } from "@/i18n/navigation";
import {
  ApiError,
  claimResumes,
  getMe,
  listResumes,
  logout,
  type Resume,
} from "@/lib/api";

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!me) {
          router.replace("/login");
          return;
        }
        if (cancelled) return;
        setEmail(me.email ?? null);
        try {
          await claimResumes();
        } catch {
          /* claim is best-effort */
        }
        const items = await listResumes();
        if (!cancelled) setResumes(items);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : t("needLogin"));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  async function onLogout() {
    try {
      await logout();
    } finally {
      router.push("/");
    }
  }

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-4xl px-5 py-12">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="rr-page-title">{t("title")}</h1>
            {email ? <p className="mt-2 text-sm text-[var(--rr-muted)]">{email}</p> : null}
          </div>
          <div className="flex gap-2">
            <Link href="/new" className="rr-btn">
              {t("newResume")}
            </Link>
            <button type="button" className="rr-btn rr-btn-ghost" onClick={onLogout}>
              {t("logout")}
            </button>
          </div>
        </div>

        {error ? <p className="text-sm text-red-700 dark:text-red-400">{error}</p> : null}

        {resumes == null && !error ? (
          <p className="text-sm text-[var(--rr-muted)]">{tCommon("loading")}</p>
        ) : null}

        {resumes && resumes.length === 0 ? (
          <div className="rr-card p-10 text-sm text-[var(--rr-muted)]">{t("empty")}</div>
        ) : null}

        {resumes && resumes.length > 0 ? (
          <ul className="grid gap-3">
            {resumes.map((resume) => (
              <li key={resume.id} className="rr-card rr-row-card flex items-center justify-between gap-4 px-5 py-4">
                <div>
                  <p className="font-medium tracking-tight">{resume.title || resume.id}</p>
                  <p className="mt-1 text-xs text-[var(--rr-muted)]">
                    {resume.source === "upload" ? t("sourceUpload") : t("sourceCreate")}
                  </p>
                </div>
                <Link href={`/editor/${resume.id}`} className="rr-btn rr-btn-ghost">
                  {t("open")}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </main>
      <Footer variant="app" />
    </div>
  );
}
