"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
import { COUNTRY_KEYS, FUNCTION_KEYS, SENIORITY_KEYS } from "@/components/jobs/facet-labels";
import ApplicationList from "@/components/dashboard/ApplicationList";
import EmployerViews from "@/components/dashboard/EmployerViews";
import MatchCountCard from "@/components/dashboard/MatchCountCard";
import ResumeHealthCard from "@/components/dashboard/ResumeHealthCard";
import { fetchDashboard, type Dashboard } from "@/lib/dashboard";
import { preferenceSummary } from "@/lib/jobs";

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
    <li className="flex items-center justify-between gap-3" data-testid="dashboard-tailor">
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
  const tJobs = useTranslations("jobs");
  const router = useRouter();
  const toast = useToast();
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [doomed, setDoomed] = useState<Doomed | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const reloadDashboard = useCallback(async () => {
    try {
      setDash(await fetchDashboard());
      setError(null);
    } catch {
      // A failed refresh after a rebuild leaves the last good view up; the
      // card's own rebuild error already told the person what went wrong.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    // The dashboard's own data is one request. It starts at once, beside the
    // resume list below, rather than behind getMe -> claimResumes.
    const first = fetchDashboard();
    (async () => {
      try {
        const next = await first;
        if (!cancelled) {
          setDash(next);
          setError(null);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : t("needLogin"));
      }
    })();
    getMe()
      .then((me) => {
        if (!cancelled) setEmail(me?.email ?? null);
      })
      .catch(() => {});
    // The file manager still needs a just-signed-in guest's anonymous resumes
    // claimed first, so claim -> list stays a chain, but off the critical path
    // of everything above it.
    (async () => {
      try {
        try {
          await claimResumes();
        } catch {
          /* claim is best-effort */
        }
        const items = await listResumes();
        if (cancelled) return;
        setResumes(items);
        // A claim may have just given this person a master the first
        // dashboard response did not know about: refetch only when it said
        // there was none and the list now shows one.
        const firstDash = await first.catch(() => null);
        if (
          firstDash &&
          !firstDash.resume_health.has_master &&
          items.some((r) => (r.kind ?? "master") === "master")
        ) {
          const next = await fetchDashboard().catch(() => null);
          if (!cancelled && next) {
            setDash(next);
            setError(null);
          }
        }
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

  const masterId = resumes?.find((r) => (r.kind ?? "master") === "master")?.id ?? null;

  const summary = useMemo(() => {
    if (!dash) return "";
    const label = (keys: Record<string, string>, v: string) =>
      keys[v] ? tJobs(keys[v] as Parameters<typeof tJobs>[0]) : v;
    return preferenceSummary(dash.preference, {
      seniority: (v) => label(SENIORITY_KEYS, v),
      fn: (v) => label(FUNCTION_KEYS, v),
      country: (v) => label(COUNTRY_KEYS, v),
      remote: tJobs("remote"),
      onsite: tJobs("remoteNo"),
    });
  }, [dash, tJobs]);

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
      <main className="mx-auto max-w-4xl px-4 py-10 sm:px-5 sm:py-12">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="rr-page-title">{t("title")}</h1>
            {email ? <p className="mt-2 text-sm text-muted-foreground">{email}</p> : null}
            {dash ? (
              <p className="mt-2 text-sm" data-testid="dashboard-profile">
                {summary ? (
                  <span className="text-muted-foreground">{summary} {"\u00b7"} </span>
                ) : null}
                <Link href="/preferences" className="underline">
                  {t("editPreferences")}
                </Link>
              </p>
            ) : null}
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

        {!dash && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}

        {dash ? (
          <div className="mb-10 grid gap-6">
            <MatchCountCard count={dash.match_count} hasPreference={summary !== ""} />
            <ApplicationList initial={dash.applications} />
            <ResumeHealthCard
              health={dash.resume_health}
              masterId={masterId}
              onRebuilt={reloadDashboard}
            />
            <EmployerViews views={dash.employer_views} />
          </div>
        ) : null}

        {resumes ? (
          <h2 className="mb-3 text-sm font-semibold tracking-tight text-muted-foreground">
            {t("headingResumes")}
          </h2>
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
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium tracking-tight">{master.title || master.id}</p>
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
