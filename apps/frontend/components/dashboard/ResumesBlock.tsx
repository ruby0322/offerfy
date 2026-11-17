"use client";

import { useFormatter, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import ResumeThumb from "./ResumeThumb";
import { featuredResume, lastTouched, statsFor } from "./resumeStats";
import type { Application } from "@/lib/applications";
import type { Resume } from "@/lib/api";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";

/**
 * The document the rest of the page came out of, at the foot of the page.
 *
 * It used to open the overview, which put the document ahead of everything
 * the document is FOR, and it said two things: which file you touched last
 * and when. Read last instead, it can answer a better question -- what has
 * this resume actually DONE -- and every figure for that answer is already on
 * the page: the resume list, the application rows, and the employer-view
 * count from the dashboard payload. Still no request of its own.
 *
 * WHY THE FEATURED ONE IS A MASTER. See resumeStats: counted per document,
 * "most used" is meaningless (a tailor is cut for one posting and a master is
 * never linked to an application), and counted per FAMILY it is exactly the
 * question a person means. The document they last touched did not lose its
 * place -- it is the last row, where "back to it" belongs.
 *
 * WHY A THUMBNAIL IS AFFORDABLE HERE AND WAS NOT BEFORE. ResumeThumb compiles
 * Typst per call, which is why this block refused one while it sat at the top
 * of the busiest page in the product. Below the fold the same component costs
 * nothing until someone scrolls to it: it renders on intersection and caches
 * by id and revision. One thumbnail, once a session, for the only block on
 * the page whose subject is a document you can look at.
 */

type Props = {
  /** Null while the list is still in flight, or if it failed. */
  resumes: Resume[] | null;
  /** The pipeline: where sent, replied and the named win come from. */
  applications: Application[];
  /** dashboard.employer_views.disclosure_count -- the payoff of publishing. */
  employerViews: number;
};

export default function ResumesBlock({ resumes, applications, employerViews }: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();
  // A page-shaped thumbnail beside the text is unreadable at phone width and
  // 420px tall above it; the banner crop is the phone's answer. The hook, not
  // a CSS fold, because only one of the two should ever mount.
  const narrow = useMediaQuery(NARROW_SCREEN);

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
            same thing. */}
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

  const featured = featuredResume(resumes, applications);
  if (featured === null) return null;
  const recent = lastTouched(resumes);
  const stats = statsFor(featured, resumes, applications);
  const isTailor = (featured.kind ?? "master") === "tailor";
  const masters = resumes.filter((r) => (r.kind ?? "master") === "master").length;
  // A tailor carries the role it was cut for; seven similarly named copies
  // are told apart by that line and by nothing else.
  const nameOf = (resume: Resume) =>
    ((resume.kind ?? "master") === "tailor"
      ? resume.target_label || resume.title
      : resume.title) || resume.id;
  const editedAt = Date.parse(featured.updated_at ?? "");

  // The funnel, left to right, dropping every stage that is zero rather than
  // printing it. A master nobody has cut a version from yet has none of them,
  // and then the honest line is the one this block used to carry alone: when
  // you last worked on it.
  const funnel = [
    stats.tailors > 0 ? t("resumeFamilyTailors", { count: stats.tailors }) : null,
    stats.sent > 0 ? t("resumeSent", { count: stats.sent }) : null,
    stats.replied > 0 ? t("resumeFamilyReplied", { count: stats.replied }) : null,
  ].filter((part): part is string => part !== null);
  const story =
    funnel.length > 0
      ? funnel
      : Number.isNaN(editedAt)
        ? []
        : [t("resumeEdited", { when: format.relativeTime(new Date(editedAt), new Date()) })];

  // Published is per document (it has a published revision); the view count
  // is per account, which is why it is phrased as a separate clause rather
  // than as something this one resume earned.
  const published = (featured.published_revision_id ?? null) !== null;

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

      {/* Centred against the thumbnail on a wide screen, not top-aligned: the
          page is 147px tall and the three lines beside it are ninety, so
          hanging them from the top leaves a visible hole under the last one. */}
      <div className="mt-3.5 flex min-w-0 flex-col gap-3 wide:flex-row wide:items-center wide:gap-4">
        <div className={narrow ? "min-w-0" : "w-[104px] shrink-0"}>
          <ResumeThumb
            resumeId={featured.id}
            revision={featured.updated_at ?? null}
            title={nameOf(featured)}
            variant={narrow ? "banner" : "page"}
          />
        </div>

        <div className="min-w-0 flex-1">
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
            <span className="truncate text-[0.9375rem] font-semibold">
              {nameOf(featured)}
            </span>
          </span>

          <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-5 gap-y-1">
            {story.length > 0 ? (
              <span className="text-[0.78rem] text-muted-foreground">
                {story.join(" → ")}
              </span>
            ) : (
              <span />
            )}
            <span className="text-[0.78rem] text-muted-foreground">
              <span className={published ? "font-semibold text-success" : ""}>
                {published ? t("resumePublished") : t("resumeUnpublished")}
              </span>
              {published && employerViews > 0
                ? ` · ${t("resumeViewed", { count: employerViews })}`
                : ""}
            </span>
          </div>

          {/* The only line on the overview that names a win. It is dropped
              entirely when nothing has come back -- a "0 replies" here would
              turn the one encouraging thing on the page into a scoreboard. */}
          {stats.reply ? (
            <p className="mt-2.5 break-words border-l-2 border-[color-mix(in_srgb,var(--success)_55%,var(--rule))] pl-2.5 text-[0.78rem]">
              <span className="font-semibold">
                {t("resumeReplyWon", { company: stats.reply.company })}
              </span>{" "}
              <span className="text-muted-foreground">— {stats.reply.job_title}</span>
            </p>
          ) : null}
        </div>

        <div className="flex justify-end wide:self-center">
          <Link
            className="rr-btn rr-btn-accent rr-btn-sm"
            href={`/editor/${encodeURIComponent(featured.id)}`}
          >
            {t("resumeKeepEditing")}
          </Link>
        </div>
      </div>

      {/* Where the old block's one job went: whatever you had open last, when
          that is not the document above. */}
      {recent && recent.id !== featured.id ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-rule pt-2.5">
          <span className="min-w-0 truncate text-[0.78rem] text-muted-foreground">
            {t("resumeRecent")} · <span className="text-foreground">{nameOf(recent)}</span>
          </span>
          <Link
            className="rr-btn rr-btn-ghost rr-btn-sm"
            href={`/editor/${encodeURIComponent(recent.id)}`}
          >
            {t("resumeBackTo")}
          </Link>
        </div>
      ) : null}
    </section>
  );
}
