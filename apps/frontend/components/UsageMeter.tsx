"use client";

import type { Usage } from "@/lib/api";

type Props = {
  usage: Usage | null;
  /** "{used} of {limit} credits this week" / "{used} of {limit} AI edits" */
  label: string;
  /** "about {n} edits left" */
  editsLeftLabel: string | null;
  /** "resets Monday" */
  resetLabel: string;
};

/**
 * How much AI editing is left this week.
 *
 * Shown from the start, not only when it runs out: a meter that appears at the
 * wall is a surprise, and the point of this was transparency. The low state at
 * 20% changes the bar and nothing else — it never blocks and never interrupts.
 *
 * Renders nothing when limits are not enforced (development), rather than
 * drawing an empty meter that means nothing.
 */
export default function UsageMeter({ usage, label, editsLeftLabel, resetLabel }: Props) {
  if (!usage || !usage.enforced) return null;

  // Exactly one side of the tier is capped: a guest on a turn count, an
  // account on cost. Read whichever is set.
  const used = usage.turn_limit !== null ? usage.turns_used : usage.used_credits;
  const limit = usage.turn_limit !== null ? usage.turn_limit : usage.limit_credits;
  if (limit === null || limit <= 0) return null;

  const ratio = Math.max(0, Math.min(1, used / limit));
  const remaining = 1 - ratio;
  const state = usage.exhausted ? "empty" : remaining <= 0.2 ? "low" : "ok";

  return (
    <div className="usage-meter" data-state={state}>
      <div
        className="usage-meter-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={Math.min(used, limit)}
        aria-label={label}
      >
        <span className="usage-meter-fill" style={{ width: `${ratio * 100}%` }} />
      </div>
      <p className="usage-meter-text">
        <span>{label}</span>
        {editsLeftLabel ? <span className="usage-meter-dim">{editsLeftLabel}</span> : null}
        <span className="usage-meter-dim">{resetLabel}</span>
      </p>
    </div>
  );
}
