"use client";

import {
  buildIngestFlow,
  flowLayout,
  type DiversionKind,
  type FlowUnit,
  type IngestFlowLeaf,
} from "@/lib/admin-ingest-flow";
import type { AdminIngestRun } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

/**
 * One ingest pass drawn as what it is: a few steps, and a branch at each one.
 *
 * The arithmetic lives in `lib/admin-ingest-flow` so it can be tested without
 * a DOM, and so the one genuinely treacherous number -- `fetched - stored`,
 * which means something different per provider -- is interpreted in exactly
 * one place. This file only lays it out.
 */

const WIDTH = 680;
const LABEL_W = 104;
const NODE_W = 10;
const LEFT_X = LABEL_W + 12;
const RIGHT_X = 468;
const PAD_Y = 16;
const GAP = 10;
const MIN_BAND = 12;
const TARGET_STACK = 236;

/** Fixed locale: an ops page wants 11,213 to be the same string on every machine. */
function fmt(value: number): string {
  return value.toLocaleString("en-US");
}

function bandPath(y1: number, h1: number, y2: number, h2: number): string {
  const x1 = LEFT_X + NODE_W;
  const mx = (x1 + RIGHT_X) / 2;
  return [
    `M${x1},${y1}`,
    `C${mx},${y1} ${mx},${y2} ${RIGHT_X},${y2}`,
    `L${RIGHT_X},${y2 + h2}`,
    `C${mx},${y2 + h2} ${mx},${y1 + h1} ${x1},${y1 + h1}`,
    "Z",
  ].join(" ");
}

const LEAF_TONE: Record<IngestFlowLeaf["tone"], string> = {
  keep: "var(--chart-2)",
  drop: "var(--chart-5)",
  error: "var(--destructive)",
};

export default function IngestFlow({ rows }: { rows: AdminIngestRun[] }) {
  const t = useTranslations("admin");
  const flow = useMemo(() => buildIngestFlow(rows), [rows]);

  const unit = (value: number, kind: FlowUnit) =>
    `${fmt(value)} ${t(`flowUnit_${kind}`, { count: value })}`;

  const layout = flowLayout(flow.sources, {
    stack: TARGET_STACK,
    gap: GAP,
    minBand: MIN_BAND,
    padY: PAD_Y,
  });

  const bySource = new Map(flow.sources.map((entry) => [entry.source, entry]));
  const drawn = layout.bands.flatMap((band) => {
    const source = bySource.get(band.source);
    return source ? [{ band, source }] : [];
  });

  const footnote = (kind: DiversionKind) => flow.diversions.indexOf(kind) + 1;

  const Footnotes = ({ kinds }: { kinds: DiversionKind[] }) =>
    kinds.length === 0 ? null : <sup>{kinds.map((_, index) => index + 1).join(",")}</sup>;

  return (
    <div className="space-y-4">
      {/* The steps. One card per stage, with the branch that leaves there. */}
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {flow.steps.map((step) => (
          <li key={step.id} className="rounded-lg border border-border bg-sheet p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              {t(`flowStep_${step.id}`)}
            </p>
            <p className="mt-1 text-xl font-semibold tabular-nums">{fmt(step.value)}</p>
            <p className="text-xs text-muted-foreground">{t(`flowStepHint_${step.id}`)}</p>
            {step.branches.map((branch) => (
              <p
                key={branch.id}
                className={cn(
                  "mt-2 text-xs tabular-nums",
                  branch.value > 0 && branch.id !== "diverted"
                    ? "text-destructive"
                    : "text-muted-foreground",
                )}
              >
                {"↳ "}
                {t(`flowBranch_${branch.id}`, { value: unit(branch.value, branch.unit) })}
                {/* The headline number for a Workday pass is mostly postings we
                    kept, not postings we rejected. The caveat has to travel
                    with it, not sit alone under the table below. */}
                {branch.id === "diverted" ? <Footnotes kinds={flow.diversions} /> : null}
              </p>
            ))}
          </li>
        ))}
      </ol>

      {/* The routes. Band height is the provider's share of what was fetched. */}
      {drawn.length === 0 ? (
        <p className="rounded-lg border border-border bg-sheet p-4 text-sm text-muted-foreground">
          {t("flowEmpty")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-sheet">
          <svg
            viewBox={`0 0 ${WIDTH} ${layout.height}`}
            width={WIDTH}
            height={layout.height}
            role="img"
            aria-label={t("flowDiagramAlt", {
              fetched: fmt(flow.totals.fetched),
              stored: fmt(flow.totals.stored),
              diverted: fmt(flow.totals.diverted),
            })}
            className="min-w-[42rem] max-w-full"
          >
            {drawn.map(({ band, source }) => (
              <g key={band.source}>
                {band.storedHeight > 0 ? (
                  <path
                    d={bandPath(band.y, band.storedHeight, band.storedAt, band.storedHeight)}
                    fill="var(--chart-2)"
                    opacity={0.34}
                  />
                ) : null}
                {band.divertedHeight > 0 ? (
                  <path
                    d={bandPath(
                      band.y + band.storedHeight,
                      band.divertedHeight,
                      band.divertedAt,
                      band.divertedHeight,
                    )}
                    fill="var(--chart-5)"
                    opacity={0.26}
                  />
                ) : null}
                <rect
                  x={LEFT_X}
                  y={band.y}
                  width={NODE_W}
                  height={band.height}
                  rx={2}
                  fill="var(--chart-1)"
                />
                <text
                  x={LABEL_W}
                  y={band.y + band.height / 2 - (band.height >= 30 ? 3 : 0)}
                  textAnchor="end"
                  dominantBaseline="middle"
                  fill="currentColor"
                  fontSize="12"
                >
                  {source.source}
                </text>
                {band.height >= 30 ? (
                  <text
                    x={LABEL_W}
                    y={band.y + band.height / 2 + 12}
                    textAnchor="end"
                    dominantBaseline="middle"
                    fill="var(--muted-foreground)"
                    fontSize="11"
                  >
                    {fmt(source.fetched)}
                  </text>
                ) : null}
              </g>
            ))}

            {layout.storedHeight > 0 ? (
              <LeafNode
                y={layout.storedTop}
                height={layout.storedHeight}
                color="var(--chart-2)"
                label={t("flowLeaf_in_catalog")}
                value={fmt(flow.totals.stored)}
              />
            ) : null}
            {layout.divertedHeight > 0 ? (
              <LeafNode
                y={layout.divertedTop}
                height={layout.divertedHeight}
                color="var(--chart-5)"
                label={t("flowLeaf_diverted")}
                value={fmt(flow.totals.diverted)}
              />
            ) : null}
          </svg>
        </div>
      )}

      {/* The leaves, including the two the bands cannot carry: a board that
          never answered has no postings to draw, and a deactivation comes from
          the held catalog rather than from this pass's fetch. */}
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {flow.leaves.map((leaf) => (
          <div
            key={leaf.id}
            className={cn(
              "rounded-lg border p-3",
              leaf.tone === "error" && leaf.value > 0
                ? "border-destructive/50 bg-destructive/5"
                : "border-border bg-sheet",
            )}
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className="inline-block size-2 shrink-0 rounded-full"
                style={{ background: LEAF_TONE[leaf.tone] }}
              />
              <p className="text-xs text-muted-foreground">{t(`flowLeaf_${leaf.id}`)}</p>
            </div>
            <p className="mt-1 text-lg font-semibold tabular-nums">
              {fmt(leaf.value)}
              {leaf.id === "diverted" ? <Footnotes kinds={flow.diversions} /> : null}
            </p>
            <p className="text-xs text-muted-foreground">
              {t(`flowUnit_${leaf.unit}`, { count: leaf.value })}
            </p>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">{t("flowRoutes")}</h3>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="py-2 font-medium">{t("flowProvider")}</th>
                <th className="py-2 font-medium">{t("flowBoards")}</th>
                <th className="py-2 font-medium">{t("ingestFetched")}</th>
                <th className="py-2 font-medium">{t("flowStored")}</th>
                <th className="py-2 font-medium">{t("flowDiverted")}</th>
                <th className="py-2 font-medium">{t("flowDeactivated")}</th>
              </tr>
            </thead>
            <tbody>
              {flow.sources.map((entry) => (
                <tr key={entry.source} className="border-b border-border/70">
                  <td className="py-2">{entry.source}</td>
                  <td className="py-2 tabular-nums">
                    {entry.boardsOk}
                    {entry.boardsFailed > 0 ? (
                      <span className="text-destructive"> / {entry.boardsFailed}</span>
                    ) : null}
                  </td>
                  <td className="py-2 tabular-nums">{fmt(entry.fetched)}</td>
                  <td className="py-2 tabular-nums">{fmt(entry.stored)}</td>
                  <td className="py-2 tabular-nums text-muted-foreground">
                    {fmt(entry.diverted)}
                    {entry.diverted > 0 ? <sup>{footnote(entry.diversion)}</sup> : null}
                  </td>
                  <td className="py-2 tabular-nums">{fmt(entry.deactivated)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {flow.diversions.length > 0 ? (
          <ol className="space-y-1 text-xs text-muted-foreground">
            {flow.diversions.map((kind, index) => (
              <li key={kind}>
                <sup>{index + 1}</sup> {t(`flowNote_${kind}`)}
              </li>
            ))}
          </ol>
        ) : null}
      </div>

      {flow.pipelineErrors.length > 0 ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm font-semibold text-destructive">{t("flowPipelineErrors")}</p>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {flow.pipelineErrors.map((item, index) => (
              <li key={`${item.source}-${index}`}>
                {item.source}: {item.error ?? "—"}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function LeafNode({
  y,
  height,
  color,
  label,
  value,
}: {
  y: number;
  height: number;
  color: string;
  label: string;
  value: string;
}) {
  return (
    <g>
      <rect x={RIGHT_X} y={y} width={NODE_W} height={height} rx={2} fill={color} />
      <text
        x={RIGHT_X + NODE_W + 10}
        y={y + height / 2 - 7}
        dominantBaseline="middle"
        fill="currentColor"
        fontSize="12"
      >
        {label}
      </text>
      <text
        x={RIGHT_X + NODE_W + 10}
        y={y + height / 2 + 9}
        dominantBaseline="middle"
        fill="var(--muted-foreground)"
        fontSize="11"
      >
        {value}
      </text>
    </g>
  );
}
