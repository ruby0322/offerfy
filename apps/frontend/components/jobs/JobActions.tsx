"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, createTailor, getMe, listResumes } from "@/lib/api";
import {
  fetchApplication,
  markApplied,
  saveJob,
  unsaveJob,
  type Application,
} from "@/lib/applications";

const ACTION =
  "inline-block border border-current px-3.5 py-2 text-sm disabled:opacity-50";

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
  | { kind: "ready"; application: Application | null; masterId: string | null };

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
        const master = resumes.find((r) => r.kind === "master");
        if (!cancelled) {
          setState({ kind: "ready", application, masterId: master?.id ?? null });
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
      className="jobs-apply"
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

  const { application, masterId } = state;
  const saved = application !== null;

  function update(next: Application | null) {
    setState({ kind: "ready", application: next, masterId });
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

  async function tailor() {
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
        disabled={busy || (saved && application.status !== "saved")}
        aria-pressed={saved}
        aria-label={saved && application.status === "saved" ? t("unsave") : undefined}
      >
        {saved ? t("saved") : t("save")}
      </button>
      {masterId ? (
        <button type="button" className={ACTION} onClick={tailor} disabled={busy}>
          {application?.tailor_resume_id ? t("tailorAgain") : t("tailorForThisRole")}
        </button>
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
