"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { markFor, monogram } from "@/components/employer-marks";
import {
  buildApplicationFlow,
  employersOf,
  flowGeometry,
  type FlowLeafId,
  type FlowNodeId,
  type FlowTone,
} from "./applicationFlow";
import type { Application } from "@/lib/applications";

/**
 * The pipeline as an ops-style flow: four columns, and a band at each hop
 * whose thickness is the share of applications that took it.
 *
 * The arithmetic and the stacking are both in ./applicationFlow, tested
 * without a DOM; this file only renders. It is the same split
 * components/admin/IngestFlow makes, for the same reason.
 *
 * Every leaf carries its employers as a row of marks, capped at what fits
 * beside it with "+N" for the rest, because "No answer 9" is a fact nobody
 * can act on while nine recognisable companies are nine specific decisions.
 */

const SCALE_MAX = 7;
const SCALE_MIN = 2.5;
const TARGET_HEIGHT = 190;
const NODE_W = 10;
const COLUMNS: [number, number, number, number] = [92, 300, 470, 630];
const VIEW_W = 880;
/** Marks are 15px on an 18px pitch; the label column runs to the right edge. */
const TILE = 15;
const PITCH = 18;

const LEAF_KEY: Record<FlowLeafId, string> = {
  onlySaved: "leafOnlySaved",
  neverSent: "leafNeverSent",
  droppedEarly: "leafDroppedEarly",
  waiting: "leafWaiting",
  interviewing: "groupInterviewing",
  repliedClosed: "leafRepliedClosed",
  noAnswer: "leafNoAnswer",
};

const STAGE_KEY: Record<"tracked" | "worked" | "sent", string> = {
  tracked: "flowTracked",
  worked: "flowWorked",
  sent: "flowSent",
};

function fill(tone: FlowTone | "stage"): string {
  if (tone === "forward") return "var(--success)";
  if (tone === "stalled") return "var(--warn)";
  if (tone === "ended") return "var(--muted)";
  return "var(--clay)";
}

/** Bands are washed out so the labels on top of them stay readable. */
function bandOpacity(tone: FlowTone | "stage"): number {
  return tone === "forward" ? 0.45 : tone === "stalled" ? 0.34 : tone === "ended" ? 0.2 : 0.28;
}

function isLeaf(id: FlowNodeId): id is FlowLeafId {
  return id !== "tracked" && id !== "worked" && id !== "sent";
}

/**
 * One employer's mark, drawn in SVG rather than through EmployerLogo.
 *
 * Same two sources as that component — the brand path from employer-marks, a
 * monogram when there is none — but emitted as native SVG so it scales with
 * the viewBox and needs no foreignObject, whose sizing and namespace
 * behaviour differ between browsers. The brand paths are drawn on a 24-unit
 * grid, so they are scaled into an 11-unit box inside the 15-unit tile.
 */
function Mark({ company, x, y }: { company: string; x: number; y: number }) {
  const mark = markFor(company);
  const inner = 11;
  const inset = (TILE - inner) / 2;
  return (
    <g transform={`translate(${x},${y})`}>
      <title>{company}</title>
      <rect
        width={TILE}
        height={TILE}
        rx={3.5}
        fill="var(--paper)"
        stroke="var(--rule)"
        strokeWidth={1}
      />
      {mark ? (
        <g transform={`translate(${inset},${inset}) scale(${inner / 24})`}>
          <path d={mark.d} fill={mark.hex ?? "var(--ink)"} fillRule={mark.fillRule} />
        </g>
      ) : (
        <text
          x={TILE / 2}
          y={TILE * 0.72}
          textAnchor="middle"
          fontSize={6.5}
          fontWeight={600}
          fill="var(--clay)"
          className="font-display"
        >
          {monogram(company)}
        </text>
      )}
    </g>
  );
}

type Props = {
  rows: Application[];
  /**
   * Render without the card chrome, for embedding inside a section that is
   * already a card -- the overview puts the diagram and the ongoing rows in
   * one card, and two nested cards read as a mistake.
   */
  bare?: boolean;
};

export default function ApplicationFlow({ rows, bare = false }: Props) {
  const t = useTranslations("dashboard");
  const flow = useMemo(() => buildApplicationFlow(rows), [rows]);

  // One tall pipeline must not produce a 2000px diagram, and one short one
  // must not be a hairline: the scale is fitted to the target height and then
  // clamped, so a very large pipeline compresses rather than overflowing.
  const geometry = useMemo(() => {
    const scale =
      flow.tracked === 0
        ? SCALE_MAX
        : Math.min(SCALE_MAX, Math.max(SCALE_MIN, TARGET_HEIGHT / flow.tracked));
    return flowGeometry(flow, {
      width: VIEW_W,
      scale,
      nodeWidth: NODE_W,
      columns: COLUMNS,
      gap: 14,
      padY: 14,
      minHeight: 6,
      // A leaf's label and its mark row together; without this two
      // one-application leaves sit close enough to overlap.
      labelBlock: 34,
      markSize: TILE,
      markPitch: PITCH,
      labelInset: 8,
      overflowRoom: 26,
    });
  }, [flow]);

  if (flow.tracked === 0) {
    return bare ? (
      <p className="mt-3 text-sm text-muted-foreground" data-testid="application-flow">
        {t("flowEmpty")}
      </p>
    ) : (
      <section className="rr-card px-5 py-4" data-testid="application-flow">
        <h2 className="text-sm font-semibold tracking-tight">{t("flowHeading")}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t("flowEmpty")}</p>
      </section>
    );
  }

  const byId = new Map(geometry.nodes.map((n) => [n.id, n]));

  const Wrapper = bare ? "div" : "section";
  return (
    <Wrapper
      aria-labelledby={bare ? undefined : "applications-flow"}
      data-testid="application-flow"
      className={bare ? "" : "rr-card px-5 py-4"}
    >
      {bare ? null : (
        <h2 id="applications-flow" className="text-sm font-semibold tracking-tight">
          {t("flowHeading")}
        </h2>
      )}
      <div className="mt-3 overflow-x-auto">
        <svg
          viewBox={`0 0 ${VIEW_W} ${geometry.height}`}
          className="h-auto w-full min-w-[620px]"
          role="img"
          aria-label={t("flowAlt", {
            tracked: flow.tracked,
            sent: flow.sent,
            replied: flow.replied,
          })}
        >
          <g>
            {geometry.bands.map((band) => (
              <path
                key={`${band.from}-${band.to}`}
                d={band.d}
                fill={fill(band.tone)}
                fillOpacity={bandOpacity(band.tone)}
              />
            ))}
          </g>
          <g>
            {geometry.nodes.map((node) => (
              <rect
                key={node.id}
                x={node.x}
                y={node.y}
                width={node.w}
                height={node.h}
                rx={2}
                fill={node.tone === "stage" ? "var(--ink)" : fill(node.tone)}
              />
            ))}
          </g>
          <g className="font-sans">
            {geometry.nodes.map((node) => {
              if (!isLeaf(node.id)) {
                const stage = node.id as "tracked" | "worked" | "sent";
                return (
                  <text
                    key={`l-${node.id}`}
                    x={node.x}
                    y={node.y - 5}
                    fontSize={11}
                    fontWeight={600}
                    fill="var(--ink)"
                  >
                    {`${t(STAGE_KEY[stage] as "flowTracked")} ${node.value}`}
                  </text>
                );
              }
              // Positions come from the geometry, which is tested for label
              // collisions and for running off the edges.
              const place = node.label!;
              const { companies, extra } = employersOf(rows, node.id, place.markCapacity);
              const tone =
                node.tone === "stalled"
                  ? "var(--warn)"
                  : node.tone === "forward"
                    ? "var(--success)"
                    : "var(--muted)";
              return (
                <g key={`l-${node.id}`}>
                  <text
                    x={place.x}
                    y={place.y}
                    fontSize={10.5}
                    fontWeight={node.tone === "ended" ? 400 : 600}
                    fill={tone}
                  >
                    {`${t(LEAF_KEY[node.id] as "leafWaiting")} ${node.value}`}
                  </text>
                  {companies.map((company, i) => (
                    <Mark
                      key={`${company}-${i}`}
                      company={company}
                      x={place.x + i * PITCH}
                      y={place.markY}
                    />
                  ))}
                  {extra > 0 ? (
                    <text
                      x={place.x + companies.length * PITCH + 2}
                      y={place.markY + 11}
                      fontSize={9.5}
                      fontWeight={600}
                      fill="var(--muted)"
                    >
                      {t("flowMore", { count: extra })}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      {/* The spine in words, for a phone too narrow for the drawing and for
          anyone reading with the diagram scrolled out of view. */}
      <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {(["tracked", "worked", "sent"] as const).map((stage) => {
          const node = byId.get(stage);
          if (!node) return null;
          return (
            <span key={stage} className="[font-variant-numeric:tabular-nums]">
              <span className="font-semibold text-foreground">{node.value}</span>{" "}
              {t(STAGE_KEY[stage] as "flowTracked")}
            </span>
          );
        })}
        <span className="[font-variant-numeric:tabular-nums]">
          <span className="font-semibold text-foreground">{flow.replied}</span>{" "}
          {t("metricReplyRate")}
        </span>
      </p>
    </Wrapper>
  );
}
