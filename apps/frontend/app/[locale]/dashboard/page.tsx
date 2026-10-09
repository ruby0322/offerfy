"use client";

import { useEffect, useMemo, useState } from "react";
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

function groupResumes(resumes: Resume[]) {
  const masters = resumes.filter((r) => (r.kind ?? "master") === "master");
  const masterIds = new Set(masters.map((r) => r.id));
  const byParent = new Map<string, Resume[]>();
  const orphans: Resume[] = [];
  for (const r of resumes) {
    if (r.kind !== "tailor") continue;
    if (r.parent_resume_id && masterIds.has(r.parent_resume_id)) {
      byParent.set(r.parent_resume_id, [...(byParent.get(r.parent_resume_id) ?? []), r]);
    } else {
      orphans.push(r);
    }
  }
  return {
    groups: masters.map((master) => ({ master, tailors: byParent.get(master.id) ?? [] })),
    orphans,
  };
}

function TailorRow({ resume, openLabel, badge }: { resume: Resume; openLabel: string; badge: string }) {
  return (
    <li className="flex items-center justify-between gap-4" data-testid="dashboard-tailor">
      <p className="flex min-w-0 items-center gap-2 text-sm">
        <span className="truncate">{resume.title || resume.id}</span>
        <span className="shrink-0 rounded-sm border border-primary/40 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-primary">
          {badge}
        </span>
      </p>
      <Link href={`/editor/${resume.id}`} className="rr-btn rr-btn-ghost">
        {openLabel}
      </Link>
    </li>
  );
}

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

  const { groups, orphans } = useMemo(() => groupResumes(resumes ?? []), [resumes]);

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
            {email ? <p className="mt-2 text-sm text-muted-foreground">{email}</p> : null}
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

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {resumes == null && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}

        {resumes && resumes.length === 0 ? (
          <div className="rr-card p-10 text-sm text-muted-foreground">{t("empty")}</div>
        ) : null}

        {resumes && resumes.length > 0 ? (
          <ul className="grid gap-3">
            {groups.map(({ master, tailors }) => (
              <li key={master.id} className="rr-card rr-row-card px-5 py-4" data-testid="dashboard-master">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="font-medium tracking-tight">{master.title || master.id}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {master.source === "upload" ? t("sourceUpload") : t("sourceCreate")}
                      {tailors.length > 0 ? ` · ${t("tailorCount", { count: tailors.length })}` : ""}
                    </p>
                  </div>
                  <Link href={`/editor/${master.id}`} className="rr-btn rr-btn-ghost">
                    {t("open")}
                  </Link>
                </div>
                {tailors.length > 0 ? (
                  <ul className="mt-3 grid gap-2 border-l border-rule pl-4">
                    {tailors.map((tailor) => (
                      <TailorRow key={tailor.id} resume={tailor} openLabel={t("open")} badge={t("tailorBadge")} />
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
            {orphans.map((tailor) => (
              <li key={tailor.id} className="rr-card rr-row-card px-5 py-4">
                <ul>
                  <TailorRow resume={tailor} openLabel={t("open")} badge={t("tailorBadge")} />
                </ul>
              </li>
            ))}
          </ul>
        ) : null}
      </main>
      <Footer variant="app" />
    </div>
  );
}
