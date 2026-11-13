"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, createTailor, getMe, listResumes } from "@/lib/api";
import { groupByTrack, type Digest, type DigestItem } from "@/lib/digest";

const ACTION = "inline-block border border-current px-3 py-1.5 text-sm disabled:opacity-50";

const TRACK_KEYS = {
  strong_fit: "digestTrackStrongFit",
  new_this_week: "digestTrackNewThisWeek",
} as const;

// Keys are the backend's English tier strings: STRONG_TIERS / _NEW_TIERS in
// apps/backend/app/services/jobs/digest.py (lines 49 and 54). Rename them there
// and add the new string here; an unmapped tier renders raw.
const TIER_KEYS = {
  "Strong fit": "digestTierStrongFit",
  "Good fit": "digestTierGoodFit",
  "Worth a look": "digestTierWorthALook",
} as const;

// "loading": master resume not known yet. "guest": send to login. "ready": a
// person, with or without a master resume to tailor from.
type State =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "unknown" }
  | { kind: "ready"; masterId: string | null };

type Props = {
  digest: Digest;
  /**
   * Shown instead when there is nothing to list; the card alone decides that.
   * Defaults to nothing, which suits a standalone card, but a slot that must
   * not go blank has to pass one: omitting it leaves an empty digest as a gap.
   */
  fallback?: ReactNode;
};

export default function DigestCard({ digest, fallback = null }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const groups = useMemo(() => groupByTrack(digest.items), [digest.items]);
  const degraded = digest.status === "degraded";
  const hasItems = groups.length > 0;

  useEffect(() => {
    if (!hasItems) return;
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!me) {
          if (!cancelled) setState({ kind: "guest" });
          return;
        }
        const resumes = await listResumes();
        const master = resumes.find((r) => r.kind === "master");
        if (!cancelled) setState({ kind: "ready", masterId: master?.id ?? null });
      } catch (err) {
        if (cancelled) return;
        setState(err instanceof ApiError && err.status === 401 ? { kind: "guest" } : { kind: "unknown" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hasItems]);

  // Nothing to show is the fallback (nothing, by default). This is the only
  // place that decides a digest is empty; the page never re-derives it.
  if (!hasItems) return <>{fallback}</>;

  async function tailor(item: DigestItem) {
    if (state.kind !== "ready" || !state.masterId) return;
    setBusyId(item.job_id);
    setFailedId(null);
    try {
      const resume = await createTailor(state.masterId, {
        title: `${item.company} · ${item.job_title}`,
        job_id: item.job_id,
      });
      router.push(`/editor/${resume.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setState({ kind: "guest" });
      else setFailedId(item.job_id);
      setBusyId(null);
    }
  }

  function action(item: DigestItem) {
    if (state.kind === "guest") {
      const next = `/jobs/${encodeURIComponent(item.job_id)}`;
      return (
        <Link className={ACTION} href={`/login?next=${next}`}>
          {t("digestTailor")}
        </Link>
      );
    }
    if (state.kind === "ready" && !state.masterId) {
      return (
        <Link className={ACTION} href="/upload">
          {tJobs("tailorNeedsResume")}
        </Link>
      );
    }
    return (
      <button
        type="button"
        className={ACTION}
        // A posting can close between the build and the read.
        disabled={state.kind !== "ready" || busyId === item.job_id || !item.job_is_active}
        onClick={() => tailor(item)}
      >
        {t("digestTailor")}
      </button>
    );
  }

  function row(item: DigestItem, track: string) {
    const tierKey = item.tier ? TIER_KEYS[item.tier as keyof typeof TIER_KEYS] : undefined;
    return (
      <li
        key={`${track}:${item.job_id}`}
        className="rr-card rr-row-card flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 py-3"
        data-testid="dashboard-digest-item"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium tracking-tight">
            <Link href={`/jobs/${encodeURIComponent(item.job_id)}`}>{item.job_title}</Link>
            <span className="font-normal text-muted-foreground"> {"·"} {item.company}</span>
          </p>
          {item.tier || item.fit_score !== null ? (
            <p className="mt-1 flex flex-wrap gap-x-3 text-xs font-semibold">
              {item.tier ? <span>{tierKey ? t(tierKey) : item.tier}</span> : null}
              {item.fit_score !== null ? (
                <span>{t("digestScore", { score: item.fit_score })}</span>
              ) : null}
            </p>
          ) : null}
          {item.reason ? <p className="mt-1 break-words text-sm">{item.reason}</p> : null}
          {item.gap ? (
            <p className="mt-1 break-words text-sm text-muted-foreground">
              <span className="font-semibold">{t("digestGapLabel")}</span> {item.gap}
            </p>
          ) : null}
          {!item.job_is_active ? (
            <p className="mt-1 text-xs text-muted-foreground">{t("jobInactive")}</p>
          ) : null}
          {failedId === item.job_id ? (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {tJobs("actionFailed")}
            </p>
          ) : null}
        </div>
        <div className="shrink-0">{action(item)}</div>
      </li>
    );
  }

  return (
    <section data-testid="dashboard-digest" aria-labelledby="dash-digest">
      <h2 id="dash-digest" className="mb-3 text-lg font-semibold tracking-tight">
        {t("digestHeading")}
      </h2>
      {degraded ? (
        // Before any track heading: an unlabelled list reads as ranked.
        <p className="mb-3 text-sm text-muted-foreground" data-testid="dashboard-digest-degraded">
          {t("digestDegraded")}
        </p>
      ) : null}
      {state.kind === "unknown" ? (
        <p className="mb-3 text-sm" role="alert">
          {tJobs("actionFailed")}
        </p>
      ) : null}
      <div className="grid gap-5">
        {groups.map((group) => {
          const key = TRACK_KEYS[group.track as keyof typeof TRACK_KEYS];
          return (
            <div key={group.track} data-testid={`dashboard-digest-${group.track}`}>
              {degraded ? null : (
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {/* A track this build does not know keeps its raw name. */}
                  {key ? t(key) : group.track} {"·"} {group.items.length}
                </h3>
              )}
              <ul className="grid gap-2">{group.items.map((item) => row(item, group.track))}</ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
