"use client";

import { useFormatter, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import type { Application } from "@/lib/applications";
import type { Resume } from "@/lib/api";

/**
 * The document the search runs on, on the page you open first.
 *
 * The overview deliberately holds no figures you have to study -- resume
 * state went to Profile and the list of documents to Resumes -- but the one
 * thing neither tab can do is tell you, on arrival, that there is nothing to
 * match against. So this block carries exactly two sentences: either "there
 * is no resume yet, here are the two doors", or "this is the one you last
 * touched, here is when".
 *
 * "Most used" is not the measure, and the data is why. The only usage signal
 * is `applications.tailor_resume_id`, and a tailored resume exists for one
 * posting, so almost every tailor scores 1 while a master -- the document
 * actually being worked on -- is never linked to an application at all and
 * scores 0 forever. Ranking by that puts a pile of tied tailors on top and
 * the master last. `updated_at` is the honest reading of "active", and it is
 * also the timestamp worth showing, so the sort key and the fact on screen
 * are the same thing. The send count stays as a supporting clause, dropped
 * when it is zero rather than shown as a 0.
 *
 * Nothing here costs a request: the overview already lists resumes (it needs
 * the master's id to route a publish step) and already holds the application
 * rows, which is where the counts come from.
 */

type Props = {
  /** Null while the list is still in flight, or if it failed. */
  resumes: Resume[] | null;
  /** The pipeline, for "sent N times". */
  applications: Application[];
};

function stamp(resume: Resume): number {
  const at = Date.parse(resume.updated_at ?? "");
  return Number.isNaN(at) ? 0 : at;
}

/** The one the person last worked on; sort is stable, so ties keep API order. */
function mostRecent(resumes: Resume[]): Resume {
  return [...resumes].sort((a, b) => stamp(b) - stamp(a))[0];
}

export default function ResumesBlock({ resumes, applications }: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();

  // Not an empty state: a list still loading, or one that failed, must not
  // tell someone with five resumes that they have none. Silence is the right
  // failure here -- the tab that owns the documents will say so properly.
  if (resumes === null) return null;

  if (resumes.length === 0) {
    return (
      <section className="rr-card px-5 py-4" data-testid="dashboard-resumes">
        <h2 className="text-[0.9375rem] font-semibold tracking-tight">
          {t("headingResumes")}
        </h2>
        <p className="mt-1.5 text-sm text-muted-foreground">{t("resumesNoneYet")}</p>
        {/* Both doors, and both outlined: the filled button on this page
            belongs to the next-action band, which is already naming this
            same thing one block above. */}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link className="rr-btn rr-btn-accent rr-btn-sm" href="/upload">
            {t("resumesUpload")}
          </Link>
          <Link className="rr-btn rr-btn-ghost rr-btn-sm" href="/create">
            {t("resumesCreate")}
          </Link>
        </div>
      </section>
    );
  }

  const active = mostRecent(resumes);
  const isTailor = (active.kind ?? "master") === "tailor";
  const masters = resumes.filter((r) => (r.kind ?? "master") === "master").length;
  // A tailor carries the role it was cut for; seven similarly named copies
  // are told apart by that line and by nothing else.
  const name = (isTailor ? active.target_label || active.title : active.title) || active.id;
  const sent = applications.filter((a) => a.tailor_resume_id === active.id).length;
  const at = stamp(active);

  // Two facts, not three. This row also carried the active master's own
  // tailor count until it went on screen: almost everyone has exactly one
  // master, so that number is the same one the heading just gave, a line
  // above, and reads as a stutter. The heading owns the family; this line
  // owns when it was last touched and whether it has been sent.
  const facts = [
    at > 0 ? t("resumeEdited", { when: format.relativeTime(new Date(at), new Date()) }) : null,
    sent > 0 ? t("resumeSent", { count: sent }) : null,
  ].filter((part): part is string => part !== null);

  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-resumes">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[0.9375rem] font-semibold tracking-tight">
          {t("headingResumes")}{" "}
          <span className="font-normal text-muted-foreground">
            · {t("resumesCount", { masters, tailors: resumes.length - masters })}
          </span>
        </h2>
        <Link
          href="/dashboard/resumes"
          className="text-xs text-muted-foreground underline"
        >
          {t("openResumes")}
        </Link>
      </div>
      <div className="mt-3 flex min-w-0 flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-2">
            <span
              className={`flex-none rounded-full border px-1.5 text-[0.6875rem] font-semibold ${
                isTailor
                  ? "border-[color-mix(in_srgb,var(--chart-3)_40%,var(--rule))] text-chart-3"
                  : "border-[color-mix(in_srgb,var(--clay)_40%,var(--rule))] text-primary"
              }`}
            >
              {isTailor ? t("tailorBadge") : t("masterBadge")}
            </span>
            <span className="truncate text-[0.9375rem] font-semibold">{name}</span>
          </span>
          {facts.length > 0 ? (
            <span className="text-[0.78rem] text-muted-foreground">
              {facts.join(" · ")}
            </span>
          ) : null}
        </div>
        <Link
          className="rr-btn rr-btn-accent rr-btn-sm"
          href={`/editor/${encodeURIComponent(active.id)}`}
        >
          {t("resumeKeepEditing")}
        </Link>
      </div>
    </section>
  );
}
