"use client";

import type { ReactNode } from "react";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { Link } from "@/i18n/navigation";

/**
 * One role, in the shape every list of roles on the dashboard uses.
 *
 * There were three of these: the overview's two columns, the Matches tab's
 * new arrivals, and DigestCard's own card-per-row. They disagreed about
 * whether a row has a logo, where the score sits and how wide the action is,
 * so the two halves of the Matches tab did not line up with each other or
 * with the overview.
 *
 * The slots are fixed in order: mark, name, score, action. Score and action
 * have a floor rather than a fixed width -- that keeps the numbers in a
 * readable column while letting a longer label (every CJK one) push the slot
 * out instead of wrapping inside it.
 *
 * An UNSCORED row has no score slot at all. It used to carry an em dash and a
 * "not scored yet" tooltip, which reads as a number on its way -- but the
 * daily-ingest list this shape also serves is never scored, and whether any
 * posting in it is ever scored depends on a different ordering and a cap it
 * knows nothing about (see build_digests.candidates). A blank where a number
 * goes was making a promise. The slot's width is a floor, not a fixed width,
 * so dropping it shortens the row rather than leaving a hole.
 *
 * A list used to be scored or unscored throughout, so no row lost alignment
 * against its neighbours. That stopped being true when the sweep landed: the
 * overview's right column holds arrivals, and the sweep may have reached some
 * and not others, so a scored row and a pending one can sit side by side with
 * slightly different widths. That is the honest rendering -- a posting nobody
 * has judged has no number and must not be handed one -- but alignment down a
 * column is no longer something this component can promise.
 *
 * Items are top-aligned, not centred: a scored row carries a reason and a gap
 * and is three times the height of an unscored one, and centring would leave
 * the marks and scores floating at different heights down the list.
 */

export type MatchRowProps = {
  jobId: string;
  title: string;
  company: string;
  /** Appended after the company on the second line (location, remote...). */
  meta?: string | null;
  /** 0..1 from the weekly scoring. Null renders no score slot at all. */
  score?: number | null;
  /** Shown under the score; the backend's own tier wording. */
  tier?: string | null;
  /** Why this was picked, and what is missing. Weekly picks only. */
  reason?: string | null;
  gap?: string | null;
  gapLabel?: string;
  /** Rendered when the posting has closed since it was listed. */
  inactiveLabel?: string | null;
  /** An action that just failed, as a message under the row. */
  error?: string | null;
  action: ReactNode;
};

/**
 * A strong fit earns the only colour in the column. Exported because the card
 * deck colours the same number by the same threshold, and two copies of it
 * would let a row and a card disagree about the same posting.
 */
export const STRONG = 0.8;

export default function MatchRow({
  jobId,
  title,
  company,
  meta,
  score = null,
  tier,
  reason,
  gap,
  gapLabel,
  inactiveLabel,
  error,
  action,
}: MatchRowProps) {
  return (
    <li
      className="flex items-start gap-2.5 border-b border-rule py-2.5 last:border-b-0"
      data-testid="match-row"
    >
      <span className="shrink-0 pt-0.5">
        <EmployerLogo company={company} size="sm" />
      </span>

      <span className="min-w-0 flex-1">
        <Link
          href={`/jobs/${encodeURIComponent(jobId)}`}
          className="block truncate text-[0.8125rem] font-medium"
        >
          {title}
        </Link>
        <span className="block truncate text-xs text-muted-foreground">
          {company}
          {meta ? ` · ${meta}` : ""}
        </span>
        {reason ? (
          <span className="mt-1 block break-words text-[0.8125rem]">{reason}</span>
        ) : null}
        {gap ? (
          <span className="mt-0.5 block break-words text-xs text-muted-foreground">
            {gapLabel ? <span className="font-semibold">{gapLabel}</span> : null} {gap}
          </span>
        ) : null}
        {inactiveLabel ? (
          <span className="mt-0.5 block text-xs text-muted-foreground">{inactiveLabel}</span>
        ) : null}
        {error ? (
          <span className="mt-0.5 block text-xs text-destructive" role="alert">
            {error}
          </span>
        ) : null}
      </span>

      {/* The score and the action share one band, `min-h-7`, and centre
          inside it. Without that the number is a `leading-none` 17px box and
          the button is a 28px one, both hung from the top of the row, so the
          number floats about six pixels above the button beside it. The tier
          hangs below the band rather than inside it, so it cannot pull the
          number off that centre line. */}
      {score === null && !tier ? null : (
        <span className="min-w-8 shrink-0 text-right">
          {score === null ? null : (
            <span className="flex min-h-7 items-center justify-end">
              <span
                className={`font-display text-[1.05rem] font-medium leading-none [font-variant-numeric:tabular-nums] ${
                  score >= STRONG ? "text-success" : ""
                }`}
              >
                {Math.round(score * 100)}
              </span>
            </span>
          )}
          {tier ? (
            <span className="mt-0.5 block text-[0.625rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
              {tier}
            </span>
          ) : null}
        </span>
      )}

      <span className="flex min-h-7 min-w-[4.75rem] shrink-0 items-center justify-end">
        {action}
      </span>
    </li>
  );
}
