"use client";

import { useEffect, useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import ConfirmDeleteDialog from "@/components/ConfirmDeleteDialog";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { useToast } from "@/components/Toaster";
import { Link, useRouter } from "@/i18n/navigation";
import {
  ApiError,
  claimResumes,
  deleteResume,
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

function DeleteButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-testid="dashboard-delete"
      className="rr-btn rr-btn-ghost text-muted-foreground hover:text-destructive"
      onClick={onClick}
    >
      <Trash2 aria-hidden="true" className="size-4" />
    </button>
  );
}

function TailorRow({
  resume,
  openLabel,
  badge,
  deleteLabel,
  onDelete,
}: {
  resume: Resume;
  openLabel: string;
  badge: string;
  deleteLabel: string;
  onDelete: () => void;
}) {
  return (
    <li className="flex items-center justify-between gap-4" data-testid="dashboard-tailor">
      <p className="flex min-w-0 items-center gap-2 text-sm">
        <span className="truncate">{resume.title || resume.id}</span>
        <span className="shrink-0 rounded-sm border border-primary/40 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-primary">
          {badge}
        </span>
      </p>
      <div className="flex shrink-0 items-center gap-1">
        <Link href={`/editor/${resume.id}`} className="rr-btn rr-btn-ghost">
          {openLabel}
        </Link>
        <DeleteButton label={deleteLabel} onClick={onDelete} />
      </div>
    </li>
  );
}

type Doomed = { resume: Resume; tailorCount: number };

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tPicker = useTranslations("picker");
  const router = useRouter();
  const toast = useToast();
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [doomed, setDoomed] = useState<Doomed | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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
  // The badges only need explaining to someone who has just grown their first
  // tailored version, so the line appears then and stops being noise after.
  const hasTailors = groups.some((group) => group.tailors.length > 0) || orphans.length > 0;

  async function onLogout() {
    try {
      await logout();
    } finally {
      router.push("/");
    }
  }

  function askDelete(resume: Resume, tailorCount: number) {
    setDeleteError(null);
    setDoomed({ resume, tailorCount });
  }

  async function onConfirmDelete() {
    if (!doomed || deleting) return;
    const { id, title } = doomed.resume;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteResume(id);
      // The backend cascades a master's tailors, so drop them here too.
      setResumes((prev) =>
        (prev ?? []).filter((r) => r.id !== id && r.parent_resume_id !== id),
      );
      setDoomed(null);
      toast(tCommon("deletedToast", { title: title || id }));
    } catch (err) {
      setDeleteError(err instanceof ApiError && err.message ? err.message : tCommon("deleteError"));
    } finally {
      setDeleting(false);
    }
  }

  const doomedKind = doomed?.resume.kind ?? "master";

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
          <div data-testid="dashboard-empty">
            <p className="text-sm text-muted-foreground">{t("empty")}</p>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <Link href="/upload" className="rr-card rr-choice-card block p-7">
                <h2 className="text-lg font-semibold tracking-tight">{tPicker("uploadTitle")}</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {tPicker("uploadBody")}
                </p>
              </Link>
              <Link href="/create" className="rr-card rr-choice-card block p-7">
                <h2 className="text-lg font-semibold tracking-tight">{tPicker("createTitle")}</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {tPicker("createBody")}
                </p>
              </Link>
            </div>
          </div>
        ) : null}

        {resumes && resumes.length > 0 && hasTailors ? (
          <p className="mb-4 text-xs leading-5 text-muted-foreground">{t("tailorExplainer")}</p>
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
                  <div className="flex shrink-0 items-center gap-1">
                    <Link href={`/editor/${master.id}`} className="rr-btn rr-btn-ghost">
                      {t("open")}
                    </Link>
                    <DeleteButton
                      label={tCommon("deleteConfirm")}
                      onClick={() => askDelete(master, tailors.length)}
                    />
                  </div>
                </div>
                {tailors.length > 0 ? (
                  <ul className="mt-3 grid gap-2 border-l border-rule pl-4">
                    {tailors.map((tailor) => (
                      <TailorRow
                        key={tailor.id}
                        resume={tailor}
                        openLabel={t("open")}
                        badge={t("tailorBadge")}
                        deleteLabel={tCommon("deleteConfirm")}
                        onDelete={() => askDelete(tailor, 0)}
                      />
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
            {orphans.map((tailor) => (
              <li key={tailor.id} className="rr-card rr-row-card px-5 py-4">
                <ul>
                  <TailorRow
                    resume={tailor}
                    openLabel={t("open")}
                    badge={t("tailorBadge")}
                    deleteLabel={tCommon("deleteConfirm")}
                    onDelete={() => askDelete(tailor, 0)}
                  />
                </ul>
              </li>
            ))}
          </ul>
        ) : null}
      </main>
      <Footer variant="app" />
      {doomed ? (
        <ConfirmDeleteDialog
          title={tCommon("deleteTitle", { title: doomed.resume.title || doomed.resume.id })}
          description={
            doomedKind === "tailor"
              ? tCommon("deleteTailorHint")
              : doomed.tailorCount > 0
                ? tCommon("deleteMasterWithTailorsHint", { count: doomed.tailorCount })
                : tCommon("deleteMasterHint")
          }
          busy={deleting}
          error={deleteError}
          onConfirm={() => void onConfirmDelete()}
          onClose={() => setDoomed(null)}
          labels={{
            confirm: tCommon("deleteConfirm"),
            confirming: tCommon("deleting"),
            cancel: tCommon("deleteCancel"),
          }}
        />
      ) : null}
    </div>
  );
}
