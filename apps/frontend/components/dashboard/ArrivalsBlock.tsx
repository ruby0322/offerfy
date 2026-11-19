"use client";

import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import MatchRow from "./MatchRow";
import { arrivalState, groupArrivals } from "./arrivals";
import { Link } from "@/i18n/navigation";
import type { MatchArrival } from "@/lib/dashboard";

/**
 * What was scored for you since you last looked, above the weekly picks.
 *
 * Two surfaces, two jobs: this one is "what landed", the digest card below it
 * is "this week, picked and explained". A posting can sit in both; that is
 * accepted, not a bug.
 *
 * The rows are the server's word, not ours. An arrival is scored for you
 * because it matched when it was scored, so one that has since stopped
 * matching your preference still shows, and one that has closed is marked
 * rather than dropped. A row with no score is "still scoring" -- not yet --
 * which is a different promise from the old "not scored", which meant never.
 *
 * Nothing renders when there is nothing to show; the page's header line is
 * what says "nothing new" or "welcome", so this block does not repeat it.
 *
 * Built for zh-TW first: no fixed widths, the day heading and the still
 * scoring heading are plain text on one line each, and the score slot is
 * MatchRow's floor-not-width one.
 */

type Props = {
  arrivals: MatchArrival[];
};

// The server caps arrivals; a first visit can still be fifteen. Past this the
// rest open HERE, in place.
//
// They used to be a link to the Matches tab, which was the one affordance this
// block could not keep: that tab shows the weekly picks and the ingest, never
// arrivals, and the window has been marked seen by the time anyone clicks --
// so "the rest" would have been nowhere. Every arrival the server sent is
// already in this component's props, so showing the rest costs no request and
// cannot find an emptied window.
const VISIBLE = 10;

export default function ArrivalsBlock({ arrivals }: Props) {
  const t = useTranslations("dashboard");
  const format = useFormatter();
  const [expanded, setExpanded] = useState(false);

  if (arrivals.length === 0) return null;

  const shown = expanded ? arrivals : arrivals.slice(0, VISIBLE);
  const hidden = arrivals.length - shown.length;
  const groups = groupArrivals(shown);

  function tierLabel(tier: string | null): string | null {
    if (!tier) return null;
    const keys: Record<string, string> = {
      "Strong fit": "digestTierStrongFit",
      "Good fit": "digestTierGoodFit",
      "Worth a look": "digestTierWorthALook",
    };
    const key = keys[tier];
    return key ? t(key as Parameters<typeof t>[0]) : tier;
  }

  return (
    <section
      className="rr-card px-5 py-4"
      data-testid="dashboard-arrivals"
      aria-labelledby="dash-arrivals"
    >
      <h2
        id="dash-arrivals"
        className="font-display text-[1.15rem] font-medium tracking-[-0.025em]"
      >
        {t("arrivalsHeading")}
      </h2>

      {groups.map((group) => (
        <div key={group.day ?? "unscored"} className="mt-3 first:mt-2">
          {groups.length > 1 || group.day === null ? (
            <p className="text-[0.8125rem] font-bold">
              {group.day === null
                ? t("arrivalsStillScoring")
                : format.dateTime(new Date(`${group.day}T12:00:00+08:00`), {
                    month: "short",
                    day: "numeric",
                    timeZone: "Asia/Taipei",
                  })}
            </p>
          ) : null}
          <ul className="mt-1">
            {group.items.map((item) => (
              <MatchRow
                key={item.job_id}
                jobId={item.job_id}
                title={item.job_title}
                company={item.company}
                meta={item.location}
                score={item.fit_score}
                tier={tierLabel(item.tier)}
                reason={item.reason}
                gap={item.gap}
                gapLabel={t("digestGapLabel")}
                inactiveLabel={item.job_is_active ? null : t("jobInactive")}
                action={
                  <Link
                    href={`/jobs/${encodeURIComponent(item.job_id)}`}
                    className={`rr-btn rr-btn-sm ${
                      arrivalState(item) !== "scored" ? "rr-btn-ghost" : "rr-btn-accent"
                    }`}
                  >
                    {arrivalState(item) !== "scored" ? t("open") : t("digestTailor")}
                  </Link>
                }
              />
            ))}
          </ul>
        </div>
      ))}

      {hidden > 0 ? (
        <p className="mt-2 text-sm">
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => setExpanded(true)}
          >
            {t("arrivalsMore", { count: hidden })}
          </button>
        </p>
      ) : null}
    </section>
  );
}
