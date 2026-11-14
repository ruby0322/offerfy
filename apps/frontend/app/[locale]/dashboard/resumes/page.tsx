"use client";

import { useEffect, useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import ConfirmDeleteDialog from "@/components/ConfirmDeleteDialog";
import ResumeThumb from "@/components/dashboard/ResumeThumb";
import { useToast } from "@/components/Toaster";
import { Link, useRouter } from "@/i18n/navigation";
import { useStoredPreference } from "@/lib/stored-preference";
import {
  ApiError,
  claimResumes,
  deleteResume,
  listResumes,
  type Resume,
} from "@/lib/api";

/**
 * The resume manager, as its own tab rather than the bottom half of the
 * overview.
 *
 * It is reference, not news: you come here when you want a document, which is
 * exactly why it did not deserve the overview's prime space. Two views,
 * because the two questions are different -- "which one was aimed at Appier"
 * is a list question, and "which one is the two-page version" is answered by
 * looking at the page. The choice is remembered per browser.
 *
 * The master-and-tailors nesting from the old page is kept: a tailored
 * version is a copy aimed at one job, and flattening that loses the only
 * structure the list has.
 */

const VIEW_KEY = "offerfy:resumes:view";
const VIEWS = ["list", "blocks"] as const;

type View = (typeof VIEWS)[number];

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

type Doomed = { resume: Resume; tailorCount: number };

export default function ResumesPage() {
  const t = useTranslations("dashboard");
  const tCommon = useTranslations("common");
  const tPicker = useTranslations("picker");
  const format = useFormatter();
  const router = useRouter();
  const toast = useToast();
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, choose] = useStoredPreference<View>(VIEW_KEY, VIEWS, "list");
  const [doomed, setDoomed] = useState<Doomed | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        try {
          await claimResumes();
        } catch {
          /* claim is best-effort: a guest's anonymous resumes */
        }
        const items = await listResumes();
        if (!cancelled) {
          setResumes(items);
          setError(null);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : t("needLogin"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  const { groups, orphans } = useMemo(() => groupResumes(resumes ?? []), [resumes]);
  const flat = useMemo(() => {
    // Blocks read in the same order as the list: each master then its own
    // tailors, so switching view does not reshuffle the grid.
    const out: { resume: Resume; isTailor: boolean }[] = [];
    for (const { master, tailors } of groups) {
      out.push({ resume: master, isTailor: false });
      for (const tailor of tailors) out.push({ resume: tailor, isTailor: true });
    }
    for (const orphan of orphans) out.push({ resume: orphan, isTailor: true });
    return out;
  }, [groups, orphans]);

  const counts = useMemo(() => {
    const masters = groups.length;
    const tailors = (resumes ?? []).filter((r) => r.kind === "tailor").length;
    return { masters, tailors };
  }, [groups, resumes]);

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
      setResumes((prev) => (prev ?? []).filter((r) => r.id !== id && r.parent_resume_id !== id));
      setDoomed(null);
      toast(tCommon("deletedToast", { title: title || id }));
    } catch (err) {
      setDeleteError(
        err instanceof ApiError && err.message ? err.message : tCommon("deleteError"),
      );
    } finally {
      setDeleting(false);
    }
  }

  const updated = (r: Resume) =>
    r.updated_at && !Number.isNaN(Date.parse(r.updated_at))
      ? format.relativeTime(new Date(r.updated_at), new Date())
      : t("metricNone");

  const doomedKind = doomed?.resume.kind ?? "master";

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-5 sm:py-10">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="rr-page-title">{t("headingResumes")}</h1>
          {resumes ? (
            <p className="mt-2 text-sm text-muted-foreground">
              {t("resumesLead", { masters: counts.masters, tailors: counts.tailors })}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex overflow-hidden rounded-lg border border-rule">
            {VIEWS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={view === option}
                onClick={() => choose(option)}
                className={
                  view === option
                    ? "bg-foreground px-3 py-1.5 text-xs font-semibold text-background"
                    : "bg-sheet px-3 py-1.5 text-xs font-semibold text-muted-foreground"
                }
              >
                {t(option === "list" ? "viewList" : "viewBlocks")}
              </button>
            ))}
          </span>
          <Link href="/new" className="rr-btn">
            {t("newResume")}
          </Link>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {!resumes && !error ? (
        <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
      ) : null}

      {resumes && resumes.length === 0 ? (
        <div data-testid="resumes-empty">
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

      {resumes && resumes.length > 0 && view === "blocks" ? (
        <div
          className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
          data-testid="resumes-blocks"
        >
          {flat.map(({ resume, isTailor }) => (
            <div key={resume.id} className="overflow-hidden rounded-lg border border-rule bg-sheet">
              <ResumeThumb
                key={`${resume.id}:${resume.updated_at ?? ""}`}
                resumeId={resume.id}
                revision={resume.updated_at ?? null}
                title={resume.title || resume.id}
              />
              <div className="px-2.5 py-2">
                <p className="truncate text-[0.8125rem] font-semibold leading-snug">
                  {resume.title || resume.id}
                </p>
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    className={
                      isTailor
                        ? "rounded border border-primary px-1 text-[0.625rem] font-bold uppercase tracking-[0.06em] text-primary"
                        : "rounded border border-rule px-1 text-[0.625rem] font-bold uppercase tracking-[0.06em]"
                    }
                  >
                    {isTailor ? t("tailorBadge") : t("masterBadge")}
                  </span>
                  <span className="truncate">{updated(resume)}</span>
                </p>
                <div className="mt-2 flex items-center gap-1">
                  <Link href={`/editor/${resume.id}`} className="rr-btn px-2.5 py-1 text-xs">
                    {t("open")}
                  </Link>
                  <button
                    type="button"
                    aria-label={tCommon("deleteConfirm")}
                    title={tCommon("deleteConfirm")}
                    className="rr-btn rr-btn-ghost px-2 py-1 text-muted-foreground hover:text-destructive"
                    onClick={() =>
                      askDelete(
                        resume,
                        groups.find((g) => g.master.id === resume.id)?.tailors.length ?? 0,
                      )
                    }
                  >
                    <Trash2 aria-hidden="true" className="size-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {resumes && resumes.length > 0 && view === "list" ? (
        <section className="rr-card px-5 py-4" data-testid="resumes-list">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-rule text-left">
                  {(["colResume", "colKind", "colAimedAt", "colUpdated"] as const).map((key) => (
                    <th
                      key={key}
                      className="whitespace-nowrap pb-2 pr-3 text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground"
                    >
                      {t(key)}
                    </th>
                  ))}
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {flat.map(({ resume, isTailor }) => (
                  <tr key={resume.id} className="border-b border-rule last:border-b-0">
                    <td className={`py-2 pr-3 align-middle ${isTailor ? "pl-5" : ""}`}>
                      <span className="font-medium">
                        {isTailor ? "↳ " : ""}
                        {resume.title || resume.id}
                      </span>
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 align-middle">
                      <span
                        className={
                          isTailor
                            ? "inline-flex rounded-full border border-primary px-2 py-0.5 text-xs font-semibold text-primary"
                            : "inline-flex rounded-full border border-rule px-2 py-0.5 text-xs font-semibold text-muted-foreground"
                        }
                      >
                        {isTailor ? t("tailorBadge") : t("masterBadge")}
                      </span>
                    </td>
                    <td className="py-2 pr-3 align-middle text-xs text-muted-foreground">
                      {isTailor
                        ? resume.target_label || t("metricNone")
                        : t("metricNone")}
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 align-middle text-xs text-muted-foreground">
                      {updated(resume)}
                    </td>
                    <td className="whitespace-nowrap py-2 text-right align-middle">
                      <Link href={`/editor/${resume.id}`} className="rr-btn rr-btn-ghost px-2.5 py-1 text-xs">
                        {t("open")}
                      </Link>
                      <button
                        type="button"
                        aria-label={tCommon("deleteConfirm")}
                        title={tCommon("deleteConfirm")}
                        className="rr-btn rr-btn-ghost ml-1 px-2 py-1 text-muted-foreground hover:text-destructive"
                        onClick={() =>
                          askDelete(
                            resume,
                            groups.find((g) => g.master.id === resume.id)?.tailors.length ?? 0,
                          )
                        }
                      >
                        <Trash2 aria-hidden="true" className="size-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">{t("tailorExplainer")}</p>
        </section>
      ) : null}

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
    </main>
  );
}
