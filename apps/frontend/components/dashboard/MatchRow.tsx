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
 * The four slots are fixed in order and in width: mark, name, score, action.
 * Score and action have a floor rather than a fixed width -- that keeps the
 * numbers in a readable column while letting a longer label (every CJK one)
 * push the slot out instead of wrapping inside it.
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
  /** 0..1 from the weekly scoring, or null when nothing has read it yet. */
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
  /** Shown in the score slot when `score` is null. */
  unscoredTitle?: string;
  action: ReactNode;
};

/** A strong fit earns the only colour in the column. */
const STRONG = 0.8;

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
  unscoredTitle,
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

      <span className="min-w-8 shrink-0 pt-0.5 text-right">
        <span
          className={`block font-display text-[1.05rem] font-medium leading-none [font-variant-numeric:tabular-nums] ${
            score === null
              ? "text-muted-foreground"
              : score >= STRONG
                ? "text-success"
                : ""
          }`}
          title={score === null ? unscoredTitle : undefined}
        >
          {score === null ? "—" : Math.round(score * 100)}
        </span>
        {tier ? (
          <span className="mt-1 block text-[0.625rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            {tier}
          </span>
        ) : null}
      </span>

      <span className="min-w-[4.75rem] shrink-0 pt-0.5 text-right">{action}</span>
    </li>
  );
}
