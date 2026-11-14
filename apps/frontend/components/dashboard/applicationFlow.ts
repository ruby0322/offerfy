import type { Application } from "@/lib/applications";

/**
 * The pipeline read as a flow: what each stage was handed, and where the
 * applications that did not pass on went instead.
 *
 * Shaped after lib/admin-ingest-flow, and for the same reason: the arithmetic
 * is the part most likely to be quietly wrong, so it lives in a module that
 * can be tested without a DOM, and the one treacherous judgement is made in
 * exactly one place.
 *
 * Here that judgement is what counts as a reply. It is `replied_at`, never a
 * status: closing a row overwrites its status, so afterwards a rejection and
 * a silence are both `closed`. A row whose reply predates the column reads as
 * no reply, which understates rather than invents — see
 * app.models.Application.replied_at.
 *
 * The second judgement is that applying does not require tailoring. The
 * outbound click on a job page creates a row and sends it straight to
 * `applied` with no tailored resume, so the sent node takes input from two
 * places and a diagram drawing tailored -> sent as the only path would lose
 * those rows. `sentWithoutTailor` is that second path, drawn as its own band.
 */

/** Where an application finally came to rest. Every row is in exactly one. */
export type FlowLeafId =
  /** Still at `applied`: sent, nothing heard yet. */
  | "waiting"
  /** At `interviewing`. */
  | "interviewing"
  /** Closed, and the employer had answered. */
  | "repliedClosed"
  /** Closed after sending, with no answer ever recorded. */
  | "noAnswer"
  /** Tailored and never sent. */
  | "neverSent"
  /** Saved and never tailored. */
  | "onlySaved"
  /** Closed without ever being sent. */
  | "droppedEarly";

export type FlowTone = "forward" | "stalled" | "ended";

/** A leaf's tone drives its colour: amber is a stall only the person can clear. */
export const LEAF_TONE: Record<FlowLeafId, FlowTone> = {
  interviewing: "forward",
  repliedClosed: "ended",
  noAnswer: "ended",
  waiting: "stalled",
  neverSent: "stalled",
  onlySaved: "ended",
  droppedEarly: "ended",
};

export type ApplicationFlow = {
  /** Every row the person has. */
  tracked: number;
  /** Rows with a tailored resume linked, whether or not they were sent. */
  tailored: number;
  /** Rows ever sent — `applied_at` present, whatever the status is now. */
  sent: number;
  /** Of those, how many the employer answered. */
  replied: number;
  /** Sent after tailoring: the main path into the sent node. */
  sentWithTailor: number;
  /** Sent with no tailored resume: the second path into the sent node. */
  sentWithoutTailor: number;
  /** Terminal buckets; these sum to `tracked`. */
  leaves: Record<FlowLeafId, number>;
};

const EMPTY_LEAVES: Record<FlowLeafId, number> = {
  waiting: 0,
  interviewing: 0,
  repliedClosed: 0,
  noAnswer: 0,
  neverSent: 0,
  onlySaved: 0,
  droppedEarly: 0,
};

/**
 * Which bucket one row ends in. Total, and deliberately so: a status this
 * build does not know still lands somewhere rather than vanishing from a
 * diagram whose bands are supposed to account for every application.
 */
export function leafOf(row: Application): FlowLeafId {
  if (row.status === "interviewing") return "interviewing";
  if (row.status === "applied") return "waiting";
  if (row.status === "closed") {
    if (row.replied_at !== null) return "repliedClosed";
    return row.applied_at !== null ? "noAnswer" : "droppedEarly";
  }
  if (row.status === "tailored") return "neverSent";
  if (row.status === "saved") return "onlySaved";
  // An unknown status that was nonetheless sent is still a sent application.
  if (row.applied_at !== null) return row.replied_at !== null ? "repliedClosed" : "waiting";
  return row.tailor_resume_id !== null ? "neverSent" : "onlySaved";
}

export function buildApplicationFlow(rows: Application[]): ApplicationFlow {
  const leaves = { ...EMPTY_LEAVES };
  let tailored = 0;
  let sent = 0;
  let replied = 0;
  let sentWithTailor = 0;
  let sentWithoutTailor = 0;

  for (const row of rows) {
    leaves[leafOf(row)] += 1;
    if (row.tailor_resume_id !== null) tailored += 1;
    if (row.replied_at !== null) replied += 1;
    if (row.applied_at !== null) {
      sent += 1;
      if (row.tailor_resume_id !== null) sentWithTailor += 1;
      else sentWithoutTailor += 1;
    }
  }

  return {
    tracked: rows.length,
    tailored,
    sent,
    replied,
    sentWithTailor,
    sentWithoutTailor,
    leaves,
  };
}

/**
 * The employers behind one leaf, in the order the rows arrived, capped at
 * `limit` with the remainder reported as `extra`.
 *
 * The cap is the caller's, because it is a question about width rather than
 * about data: the diagram fits as many marks as the space beside a leaf
 * takes, then says "+N".
 */
export type LeafEmployers = { companies: string[]; extra: number };

export function employersOf(
  rows: Application[],
  leaf: FlowLeafId,
  limit: number,
): LeafEmployers {
  const all: string[] = [];
  for (const row of rows) {
    if (leafOf(row) === leaf) all.push(row.company);
  }
  const capped = limit <= 0 ? [] : all.slice(0, limit);
  return { companies: capped, extra: all.length - capped.length };
}

// --------------------------------------------------------------------------
// geometry
// --------------------------------------------------------------------------
//
// Plain numbers, computed here rather than in the component, for the reason
// lib/admin-ingest-flow gives for the same split: the stacking is the part
// most likely to be quietly wrong (bands that cross, a stack taller than its
// box) and is worth testing without a DOM.
//
// The diagram is three hops, derived from the leaf partition so that it
// conserves exactly:
//
//   tracked ─┬─ onlySaved
//            └─ worked ─┬─ neverSent
//                       ├─ droppedEarly
//                       └─ sent ─┬─ waiting
//                                ├─ interviewing
//                                ├─ repliedClosed
//                                └─ noAnswer
//
// `sent` here is the sum of its four outcomes rather than
// ApplicationFlow.sent. The two agree except for a row stamped as replied
// with no send date, which the diagram places by its leaf; defining the node
// as the sum is what guarantees the bands account for every row.

export type FlowNodeId = "tracked" | "worked" | "sent" | FlowLeafId;

export type FlowNodeBox = {
  id: FlowNodeId;
  value: number;
  x: number;
  y: number;
  w: number;
  h: number;
  /** "stage" for the three spine nodes; a leaf carries its own tone. */
  tone: FlowTone | "stage";
  /**
   * Where this node's text and employer marks go. Computed here rather than
   * in the component so the thing most likely to break — a label colliding
   * with the leaf below it, or a mark row running into the next column — is
   * checked by a test instead of by eye.
   *
   * Absent on the three spine nodes, whose labels sit above them.
   */
  label?: {
    x: number;
    /** Text baseline. */
    y: number;
    /** Top of the employer mark row. */
    markY: number;
    /** How many marks fit before the next column; the rest become "+N". */
    markCapacity: number;
  };
};

export type FlowBandPath = { from: FlowNodeId; to: FlowNodeId; d: string; tone: FlowTone | "stage" };

export type FlowGeometry = {
  width: number;
  height: number;
  nodes: FlowNodeBox[];
  bands: FlowBandPath[];
};

export type FlowGeometryOptions = {
  width: number;
  /** Pixels per application. */
  scale: number;
  nodeWidth: number;
  /** Left edge of each of the four columns. */
  columns: [number, number, number, number];
  /** Vertical gap between two things in the same column. */
  gap: number;
  padY: number;
  /**
   * Floor on a node's height, so a single application beside ninety is a
   * visible sliver rather than a sub-pixel line that reads as absent.
   */
  minHeight: number;
  /**
   * Vertical room a leaf's label and its row of employer marks need
   * together. Two leaves of one application each would otherwise sit
   * `minHeight + gap` apart and their labels would overlap, which is a
   * collision the band arithmetic cannot see.
   */
  labelBlock: number;
  /** Side of one employer mark, and the stride between two of them. */
  markSize: number;
  markPitch: number;
  /** Gap between a node and its label, and room reserved for the "+N". */
  labelInset: number;
  overflowRoom: number;
};

function bandPath(
  x1: number,
  y1: number,
  h1: number,
  x2: number,
  y2: number,
  h2: number,
): string {
  const mx = Math.round((x1 + x2) / 2);
  return [
    `M${x1},${y1}`,
    `C${mx},${y1} ${mx},${y2} ${x2},${y2}`,
    `L${x2},${y2 + h2}`,
    `C${mx},${y2 + h2} ${mx},${y1 + h1} ${x1},${y1 + h1}`,
    "Z",
  ].join(" ");
}

/**
 * Lay the flow out left to right. Hop order inside each column is: the spine
 * node first, then its leaves top to bottom, and each band leaves its source
 * in that same order, so no two bands cross.
 */
export function flowGeometry(
  flow: ApplicationFlow,
  opts: FlowGeometryOptions,
): FlowGeometry {
  const {
    width,
    scale,
    nodeWidth: w,
    columns,
    gap,
    padY,
    minHeight,
    labelBlock,
    markSize,
    markPitch,
    labelInset,
    overflowRoom,
  } = opts;
  const leaves = flow.leaves;
  const sent =
    leaves.waiting + leaves.interviewing + leaves.repliedClosed + leaves.noAnswer;
  const worked = sent + leaves.neverSent + leaves.droppedEarly;
  const tracked = worked + leaves.onlySaved;

  const nodes: FlowNodeBox[] = [];
  const bands: FlowBandPath[] = [];
  if (tracked === 0) return { width, height: padY * 2 + minHeight, nodes, bands };

  // A node with no applications in it is omitted rather than drawn as a
  // floor-height sliver claiming a path nothing took.
  const size = (value: number) => (value === 0 ? 0 : Math.max(minHeight, value * scale));

  /**
   * How many marks fit beside a leaf: to the next column, or to the right
   * edge for the last one. One rule, so the wide outcomes hold more than the
   * narrow mid-flow stalls without either number being picked by hand.
   */
  const capacityAt = (x: number): number => {
    const next = columns.find((c) => c > x + w);
    const right = next === undefined ? width : next;
    return Math.max(0, Math.floor((right - x - w - labelInset - overflowRoom) / markPitch));
  };

  const put = (
    id: FlowNodeId,
    value: number,
    x: number,
    y: number,
    tone: FlowTone | "stage",
  ): FlowNodeBox | null => {
    const h = size(value);
    if (h === 0) return null;
    const box: FlowNodeBox = { id, value, x, y, w, h, tone };
    if (tone !== "stage") {
      box.label = {
        x: x + w + labelInset,
        y: y + 9,
        markY: y + 14,
        markCapacity: capacityAt(x),
      };
    }
    nodes.push(box);
    return box;
  };

  // Column 0: everything.
  const trackedBox = put("tracked", tracked, columns[0], padY, "stage")!;

  /**
   * Place one column's spine node and leaves, and band them in from the
   * source. `cursor` walks down the source node so each band leaves from its
   * own slice; destinations stack from the column top with `gap` between.
   */
  function hop(
    source: FlowNodeBox,
    x: number,
    onward: { id: FlowNodeId; value: number } | null,
    leafIds: FlowLeafId[],
  ): FlowNodeBox | null {
    let cursor = source.y;
    let top = padY;
    let onwardBox: FlowNodeBox | null = null;
    const order: { id: FlowNodeId; value: number; tone: FlowTone | "stage" }[] = [];
    if (onward) order.push({ ...onward, tone: "stage" });
    for (const id of leafIds) order.push({ id, value: leaves[id], tone: LEAF_TONE[id] });

    for (const item of order) {
      const h = size(item.value);
      if (h === 0) continue;
      const box = put(item.id, item.value, x, top, item.tone)!;
      // The slice this band takes out of the source is proportional to the
      // source's drawn height, not to the destination's: a floored
      // destination must not claim more of the source than it holds.
      const slice = Math.max(
        minHeight,
        (source.h * item.value) / Math.max(source.value, 1),
      );
      bands.push({
        from: source.id,
        to: box.id,
        d: bandPath(source.x + w, cursor, slice, x, box.y, box.h),
        tone: item.tone,
      });
      cursor += slice;
      // A leaf has to clear its own label and mark row, not just its band.
      top += item.tone === "stage" ? h + gap : Math.max(h + gap, labelBlock);
      if (onward && item.id === onward.id) onwardBox = box;
    }
    return onwardBox;
  }

  const workedBox = hop(trackedBox, columns[1], { id: "worked", value: worked }, [
    "onlySaved",
  ]);
  const sentBox = workedBox
    ? hop(workedBox, columns[2], { id: "sent", value: sent }, [
        "neverSent",
        "droppedEarly",
      ])
    : null;
  if (sentBox) {
    hop(sentBox, columns[3], null, [
      "interviewing",
      "repliedClosed",
      "noAnswer",
      "waiting",
    ]);
  }

  // The box has to hold the deepest label row as well as the tallest node:
  // a one-application leaf at the bottom is 6px of bar and 29px of text.
  const bottom = nodes.reduce(
    (max, n) =>
      Math.max(max, n.y + n.h, n.label ? n.label.markY + markSize : 0),
    padY,
  );
  return { width, height: Math.ceil(bottom + padY), nodes, bands };
}
