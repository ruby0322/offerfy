"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link, useRouter } from "@/i18n/navigation";
import TailorMenu, { type TailorMaster } from "@/components/jobs/TailorMenu";
import { ApiError, createTailor, getMe, listResumes } from "@/lib/api";
import {
  fetchApplication,
  markApplied,
  saveJob,
  unsaveJob,
  type Application,
} from "@/lib/applications";

// One shape for every control in the row. The old utility string differed
// from the apply link in padding, font size and corner, so the row read as
// three unrelated controls; .jobs-action fixes the height too, because a
// link and a button do not agree on line-height.
const ACTION = "jobs-action";

type Props = {
  jobId: string;
  applyUrl: string;
  /** Public posting data, so the server can pass it; names the tailored copy. */
  title: string;
  company: string;
  /** The translated label of the outbound apply link. */
  applyLabel: string;
};

// "loading": nothing known yet. "guest": render links to login. "ready": a
// person, with their row for this posting (or none) and their master resume.
type State =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "unknown" }
  | { kind: "ready"; application: Application | null; masters: Master[] };

/** A master resume the person could tailor from. */
type Master = TailorMaster;

// The server component cannot know any of this: /jobs/[id] is public and
// rendered without the visitor's cookie. So it is loaded here, on mount.
export default function JobActions({ jobId, applyUrl, title, company, applyLabel }: Props) {
  const t = useTranslations("jobs");
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!me) {
          if (!cancelled) setState({ kind: "guest" });
          return;
        }
        const [application, resumes] = await Promise.all([
          fetchApplication(jobId),
          listResumes(),
        ]);
        // Every master, not the first one found: a person with a backend
        // master and a new-grad master was silently always tailoring from
        // whichever the list happened to return first.
        const masters: Master[] = resumes
          .filter((r) => r.kind === "master")
          .map((r) => ({
            id: r.id,
            title: r.title ?? null,
            updatedAt: r.updated_at ?? null,
          }));
        if (!cancelled) {
          setState({ kind: "ready", application, masters });
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          setState({ kind: "guest" });
        } else {
          // Failed to load: assert nothing. A "Save" would be wrong for
          // someone who has saved this posting already.
          setState({ kind: "unknown" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // Present in every state, and in the server-rendered HTML, so the outbound
  // link is always there for a guest and for a crawler. Only a signed-in
  // person's click is recorded.
  const applyLink = (
    <a
      className="jobs-action is-primary"
      href={applyUrl}
      rel="noopener noreferrer"
      target="_blank"
      onClick={state.kind === "ready" ? applyClicked : undefined}
    >
      {applyLabel}
    </a>
  );

  const loginHref = `/login?next=/jobs/${encodeURIComponent(jobId)}`;

  if (state.kind === "guest") {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Link className={ACTION} href={loginHref}>
          {t("save")}
        </Link>
        <Link className={ACTION} href={loginHref}>
          {t("tailorForThisRole")}
        </Link>
        {applyLink}
      </div>
    );
  }

  if (state.kind === "unknown") {
    return (
      <div className="flex flex-wrap items-center gap-3">
        {applyLink}
        <p className="basis-full text-sm" role="alert">
          {t("actionFailed")}
        </p>
      </div>
    );
  }

  if (state.kind === "loading") {
    return <div className="flex flex-wrap items-center gap-3">{applyLink}</div>;
  }

  const { application, masters } = state;
  const saved = application !== null;
  const pastSaved = application !== null && application.status !== "saved";

  function statusLabel(status: Application["status"]): string {
    switch (status) {
      case "tailored":
        return t("statusTailored");
      case "applied":
        return t("statusApplied");
      case "interviewing":
        return t("statusInterviewing");
      case "closed":
        return t("statusClosed");
      default:
        return status;
    }
  }

  function update(next: Application | null) {
    setState({ kind: "ready", application: next, masters });
  }

  async function toggleSave() {
    setBusy(true);
    setError(null);
    try {
      if (saved) {
        await unsaveJob(jobId);
        update(null);
      } else {
        update(await saveJob(jobId));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setState({ kind: "guest" });
      } else if (err instanceof ApiError && err.status === 409) {
        // The row moved past saved (tailored, applied) in another tab.
        setError(t("cannotUnsave"));
        try {
          update(await fetchApplication(jobId));
        } catch {
          /* keep what is on screen */
        }
      } else {
        setError(t("actionFailed"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function tailor(masterId: string) {
    if (!masterId) return;
    setBusy(true);
    setError(null);
    try {
      const resume = await createTailor(masterId, {
        title: `${company} · ${title}`,
        job_id: jobId,
      });
      router.push(`/editor/${resume.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setState({ kind: "guest" });
      } else {
        setError(t("actionFailed"));
      }
      setBusy(false);
    }
  }

  function applyClicked() {
    // Fire and forget: the outbound link must open whether or not this lands.
    markApplied(jobId)
      .then((next) => update(next))
      .catch(() => {});
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        className={ACTION}
        onClick={toggleSave}
        // Past `saved`, the row is a status label: unsaving would only 409.
        disabled={busy || pastSaved}
        aria-pressed={pastSaved ? undefined : saved}
        // title, not aria-label: the visible text must stay the accessible name.
        title={saved && !pastSaved ? t("unsave") : undefined}
      >
        {pastSaved ? statusLabel(application.status) : saved ? t("saved") : t("save")}
      </button>
      {masters.length > 0 ? (
        <TailorMenu
          masters={masters}
          busy={busy}
          label={application?.tailor_resume_id ? t("tailorAgain") : t("tailorForThisRole")}
          heading={t("tailorFrom")}
          untitled={t("tailorUntitled")}
          onPick={tailor}
        />
      ) : (
        <Link className={ACTION} href="/upload">
          {t("tailorNeedsResume")}
        </Link>
      )}
      {application?.tailor_resume_id ? (
        <Link className={ACTION} href={`/editor/${application.tailor_resume_id}`}>
          {t("openTailor")}
        </Link>
      ) : null}
      {applyLink}
      {error ? (
        <p className="basis-full text-sm" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
