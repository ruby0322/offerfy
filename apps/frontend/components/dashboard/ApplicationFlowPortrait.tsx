"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import {
  buildApplicationFlow,
  employersOf,
  flowSpine,
  LEAF_KEY,
  LEAF_TONE,
  STAGE_KEY,
  type FlowLeafId,
  type FlowTone,
} from "./applicationFlow";
import type { Application } from "@/lib/applications";

/**
 * The same pipeline, turned on its side to read down a phone.
 *
 * Four columns and seven labelled leaves is 6px type at 390px, so below
 * NARROW_SCREEN the drawing rotates instead of shrinking: the stages run down
 * the page in the direction a phone scrolls, and everything that left at a
 * stage branches off to its right. Same spine, same leaves, same employer
 * marks -- `flowSpine` is shared with the SVG so the two cannot drift.
 *
 * What this costs is proportion. On the wide drawing you see that the
 * no-answer band is far thicker than the interviewing one; here you read 9
 * against 1 and work it out. That is a real loss, accepted because the
 * alternative at this width is a diagram you drag sideways to read.
 *
 * Every exit is one line, from 只收藏了 through 還在等回覆: a stage, then
 * what left there, three times down the page. An earlier draft drew the four
 * final outcomes as their own list with colour dots and right-aligned counts
 * while the earlier exits were indented lines -- two components for one idea,
 * which made a simple shape look complicated.
 */

/**
 * Marks shown beside a leak before the rest become "+N". Four is what the
 * widest label ("↳ 只收藏了 4") leaves room for at 390px with 138px to spare,
 * measured rather than guessed; the row wraps rather than overflows if a
 * longer translation ever eats that margin.
 */
const MARK_CAP = 4;

/** The rule beside a leak, and its text: the tones the wide drawing uses. */
const TONE_CLASS: Record<FlowTone, string> = {
  forward: "border-success text-success font-semibold",
  stalled: "border-warn text-warn font-semibold",
  answered: "border-chart-3 text-chart-3 font-semibold",
  ended: "border-rule text-muted-foreground",
};

export default function ApplicationFlowPortrait({ rows }: { rows: Application[] }) {
  const t = useTranslations("dashboard");
  const flow = useMemo(() => buildApplicationFlow(rows), [rows]);
  const spine = useMemo(() => flowSpine(flow), [flow]);

  if (flow.tracked === 0) {
    return (
      <p className="mt-3 text-sm text-muted-foreground" data-testid="application-flow">
        {t("flowEmpty")}
      </p>
    );
  }

  return (
    <div className="mt-3" data-testid="application-flow">
      {spine.map((stage) => {
        // A stage nothing reached is not drawn: an empty 已投遞 with four
        // zeroed outcomes under it claims a shape the pipeline does not have.
        if (stage.value === 0) return null;
        const leaks = stage.leaks.filter((leaf) => flow.leaves[leaf] > 0);
        return (
          <div key={stage.id} className="mt-3 first:mt-0">
            <div className="flex items-baseline justify-between gap-2 border-b border-rule pb-1.5">
              <span className="text-[0.8125rem] font-semibold">
                {t(STAGE_KEY[stage.id] as "flowTracked")}
              </span>
              <span className="font-display text-[1.15rem] font-medium leading-none [font-variant-numeric:tabular-nums]">
                {stage.value}
              </span>
            </div>
            {leaks.map((leaf) => (
              <Leak key={leaf} leaf={leaf} count={flow.leaves[leaf]} rows={rows} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Leak({
  leaf,
  count,
  rows,
}: {
  leaf: FlowLeafId;
  count: number;
  rows: Application[];
}) {
  const t = useTranslations("dashboard");
  const { companies, extra } = employersOf(rows, leaf, MARK_CAP);
  return (
    <div
      className={`ml-3 mt-[7px] flex flex-wrap items-center gap-2 border-l-2 pl-2.5 text-[0.8125rem] [font-variant-numeric:tabular-nums] ${TONE_CLASS[LEAF_TONE[leaf]]}`}
      data-testid="flow-leak"
    >
      <span>
        ↳ {t(LEAF_KEY[leaf] as "leafWaiting")} {count}
      </span>
      {companies.length > 0 ? (
        <span className="inline-flex gap-1">
          {companies.map((company, i) => (
            <EmployerLogo key={`${company}-${i}`} company={company} size="xs" />
          ))}
          {extra > 0 ? (
            <span className="self-center text-[0.6875rem] font-semibold text-muted-foreground">
              {t("flowMore", { count: extra })}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}
