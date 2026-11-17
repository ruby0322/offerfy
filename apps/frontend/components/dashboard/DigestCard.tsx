"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link, useRouter } from "@/i18n/navigation";
import MatchRow, { STRONG } from "./MatchRow";
import RoleDeck, { type DeckCard } from "./RoleDeck";
import { ApiError, createTailor, getMe, listResumes } from "@/lib/api";
import { groupByTrack, STRONG_TIER, type Digest, type DigestItem } from "@/lib/digest";

const ACTION = "rr-btn rr-btn-accent rr-btn-sm disabled:opacity-50";
// The same action on a card: it takes the full width it is given, and it is
// the SAME SIZE the list view gives it. Full-size `rr-btn` is 16px type, which
// at phone width made "See the full posting" wrap to two lines and left the
// two buttons different heights; `rr-btn-sm` is the variant that already
// carries `white-space: nowrap` and raises itself to a 40px target under a
// thumb (globals.css), so one class fixes the size, the wrap and the reach.
const CARD_ACTION = "rr-btn rr-btn-accent rr-btn-sm flex-1 disabled:opacity-50";

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
   * Suppress the card's own heading. The Matches tab frames these rows with
   * its own, and two headings for one list reads as a bug.
   */
  hideHeading?: boolean;
  /**
   * Shown instead when there is nothing to list; the card alone decides that.
   * Defaults to nothing, which suits a standalone card, but a slot that must
   * not go blank has to pass one: omitting it leaves an empty digest as a gap.
   */
  fallback?: ReactNode;
  /**
   * Rows, or one card per posting that you swipe through. Both read the same
   * groups in the same order and share the tailoring flow below; the view
   * decides the shape and nothing else.
   *
   * It lives here rather than in a second component because everything a
   * pick's action needs -- whether there is a master resume to tailor from,
   * which posting is mid-flight, which one just failed -- is this card's
   * state, and a parallel component would either duplicate it or race it.
   */
  view?: "list" | "cards";
};

export default function DigestCard({
  digest,
  fallback = null,
  hideHeading = false,
  view = "list",
}: Props) {
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

  function action(item: DigestItem, shape: "row" | "card" = "row") {
    const className = shape === "card" ? CARD_ACTION : ACTION;
    if (state.kind === "guest") {
      const next = `/jobs/${encodeURIComponent(item.job_id)}`;
      return (
        <Link className={className} href={`/login?next=${next}`}>
          {t("digestTailor")}
        </Link>
      );
    }
    if (state.kind === "ready" && !state.masterId) {
      return (
        <Link className={className} href="/upload">
          {tJobs("tailorNeedsResume")}
        </Link>
      );
    }
    return (
      <button
        type="button"
        className={className}
        // A posting can close between the build and the read.
        disabled={state.kind !== "ready" || busyId === item.job_id || !item.job_is_active}
        onClick={() => tailor(item)}
      >
        {t("digestTailor")}
      </button>
    );
  }

  /** The backend's tier wording, translated where this build knows it. */
  function tierLabel(item: DigestItem): string | null {
    if (!item.tier) return null;
    const key = TIER_KEYS[item.tier as keyof typeof TIER_KEYS];
    return key ? t(key) : item.tier;
  }

  /**
   * One pick, as a page of its own.
   *
   * Everything the row has to truncate is whole here: the title (English
   * ones run past fifty characters), the model's sentence (240, by
   * REASON_CHARS) and the gap (160). Nothing is ADDED -- no location, no
   * posted date, no description -- because a DigestItem carries none of
   * them and this card is not worth one more request per posting on the
   * busiest page in the dashboard.
   *
   * Two colours, by two different rules, on purpose: the number follows the
   * score (STRONG), the badge follows the tier the model actually assigned
   * (STRONG_TIER). They are different functions of the same four
   * probabilities, and lib/digest.ts has the scar from treating them as one.
   */
  function pickCard(item: DigestItem, track: string) {
    const tier = tierLabel(item);
    const trackKey = TRACK_KEYS[track as keyof typeof TRACK_KEYS];
    const href = `/jobs/${encodeURIComponent(item.job_id)}`;
    return (
      <article className="flex min-w-0 flex-1 flex-col rounded-lg border border-rule bg-sheet p-4">
        {/* Degraded builds hide the track everywhere, for the reason the
            notice above gives: a track name on an unranked pick is a claim
            about ordering that this week's build cannot make. */}
        {degraded ? null : (
          <p className="text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-clay">
            {trackKey ? t(trackKey) : track}
          </p>
        )}
        <div className="mt-1.5 flex min-w-0 items-start gap-2.5">
          <span className="shrink-0">
            <EmployerLogo company={item.company} />
          </span>
          <span className="min-w-0">
            <Link href={href} className="block break-words text-[0.95rem] font-semibold leading-snug">
              {item.job_title}
            </Link>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {item.company}
            </span>
          </span>
        </div>

        {item.fit_score === null && !tier ? null : (
          <div className="mt-3 flex items-center gap-2 border-y border-rule py-2.5">
            {item.fit_score === null ? null : (
              <>
                <span
                  className={`font-display text-[1.75rem] font-medium leading-none [font-variant-numeric:tabular-nums] ${
                    item.fit_score >= STRONG ? "text-success" : ""
                  }`}
                >
                  {Math.round(item.fit_score * 100)}
                </span>
                <span className="text-[0.7rem] text-muted-foreground">{t("deckScoreOutOf")}</span>
              </>
            )}
            {tier ? (
              <span
                className={`ml-auto rounded-full border px-2 py-0.5 text-[0.65rem] font-semibold ${
                  item.tier === STRONG_TIER
                    ? "border-[color-mix(in_srgb,var(--success)_45%,var(--rule))] text-success"
                    : "border-rule text-muted-foreground"
                }`}
              >
                {tier}
              </span>
            ) : null}
          </div>
        )}

        {item.reason ? (
          <p className="mt-3 break-words text-[0.8125rem] leading-relaxed">{item.reason}</p>
        ) : null}
        {item.gap ? (
          // The one sentence that says what you LACK, kept visibly apart from
          // the one that says why you fit; run together they read as a single
          // claim. The rule is warn-tinted, not destructive: missing a skill
          // is not an error.
          <p className="mt-2.5 break-words border-l-2 border-[color-mix(in_srgb,var(--warn)_55%,var(--rule))] pl-2.5 text-xs text-muted-foreground">
            <span className="font-semibold text-foreground">{t("digestGapLabel")}</span>{" "}
            {item.gap}
          </p>
        ) : null}
        {item.job_is_active ? null : (
          <p className="mt-2 text-xs text-muted-foreground">{t("jobInactive")}</p>
        )}
        {failedId === item.job_id ? (
          <p className="mt-2 text-xs text-destructive" role="alert">
            {tJobs("actionFailed")}
          </p>
        ) : null}

        {/* mt-auto: every card in the deck is as tall as the tallest, so
            without this the buttons sit at a different height on every swipe. */}
        <div className="mt-auto flex gap-2 pt-3.5">
          {action(item, "card")}
          <Link href={href} className="rr-btn rr-btn-ghost rr-btn-sm flex-1">
            {t("deckOpenJob")}
          </Link>
        </div>
      </article>
    );
  }

  function row(item: DigestItem, track: string) {
    return (
      <MatchRow
        key={`${track}:${item.job_id}`}
        jobId={item.job_id}
        title={item.job_title}
        company={item.company}
        score={item.fit_score}
        tier={tierLabel(item)}
        reason={item.reason}
        gap={item.gap}
        gapLabel={t("digestGapLabel")}
        inactiveLabel={item.job_is_active ? null : t("jobInactive")}
        error={failedId === item.job_id ? tJobs("actionFailed") : null}
        action={action(item)}
      />
    );
  }

  // Flattened in the order the headings read: the groups are already
  // de-duplicated and rank-sorted, so the deck and the list walk the same
  // postings in the same order and a reader who switches view does not have
  // to find their place again.
  const deckCards: DeckCard[] =
    view === "cards"
      ? groups.flatMap((group) =>
          group.items.map((item) => ({
            key: `${group.track}:${item.job_id}`,
            node: pickCard(item, group.track),
          })),
        )
      : [];

  // A terminus, not a card: swiping past the last pick should say so and
  // offer the next move rather than simply refusing to move. It is not
  // counted in the position, so the dots stop at the last real pick.
  const endCard = (
    <article className="flex min-w-0 flex-1 flex-col items-center justify-center rounded-lg border border-rule bg-sheet p-6 text-center">
      <p className="text-[0.95rem] font-semibold">
        {t("picksDoneHeading", { count: deckCards.length })}
      </p>
      <p className="mt-1.5 text-xs text-muted-foreground">{t("picksDoneBody")}</p>
      <div className="mt-4 flex w-full flex-col gap-2">
        <Link href="/jobs" className="rr-btn rr-btn-ghost rr-btn-sm">
          {t("browseRoles")}
        </Link>
        <Link href="/preferences" className="rr-btn rr-btn-ghost rr-btn-sm">
          {t("editPreferences")}
        </Link>
      </div>
    </article>
  );

  return (
    <section
      data-testid="dashboard-digest"
      aria-labelledby={hideHeading ? undefined : "dash-digest"}
    >
      {hideHeading ? null : (
        <h2 id="dash-digest" className="mb-3 text-lg font-semibold tracking-tight">
          {t("digestHeading")}
        </h2>
      )}
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
      {view === "cards" ? (
        <RoleDeck
          cards={deckCards}
          end={endCard}
          label={t("picksHeading")}
          testId="dashboard-digest-deck"
        />
      ) : (
      <div className="grid min-w-0 gap-5 [&>*]:min-w-0">
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
              <ul>{group.items.map((item) => row(item, group.track))}</ul>
            </div>
          );
        })}
      </div>
      )}
    </section>
  );
}
