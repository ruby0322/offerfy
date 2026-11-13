import type { AdminIngestRun } from "@/lib/api";

/**
 * One ingest pass read as a flow: what each step was handed, and where the
 * postings it did not pass on went instead.
 *
 * Everything here is derived from the per-board `job_ingest_runs` rows the
 * drilldown endpoint already returns -- no new column, no new endpoint. That
 * bounds what the diagram can honestly claim, and the bound is the whole
 * reason this module exists rather than the page doing arithmetic inline:
 * `ok_count - upserted_count` means three different things depending on the
 * provider, and a diagram that drew all three as "out of scope" would be
 * confidently wrong about ~12k Workday postings. See `DiversionKind`.
 */

/** What a provider's unstored postings are actually made of. */
export type DiversionKind =
  /**
   * Exactly the scope rules' work. The board hands over every posting, the
   * title filter drops some, the rest are stored.
   */
  | "scoped_out"
  /**
   * SmartRecruiters scopes itself by the structured `function` before paying
   * for each description, so the postings its function filter dropped were
   * never counted as fetched. What is left here is the title rule's share
   * alone, and the real out-of-scope figure is higher.
   */
  | "scoped_out_title_only"
  /**
   * Workday is incremental: a posting we already hold is reported back as
   * still-live and never re-fetched, so it counts as fetched and is never
   * upserted. The difference is therefore mostly postings we kept, not
   * postings we rejected, and the two cannot be separated from these rows.
   */
  | "held_or_scoped_out";

const DIVERSION_BY_SOURCE: Record<string, DiversionKind> = {
  greenhouse: "scoped_out",
  lever: "scoped_out",
  ashby: "scoped_out",
  smartrecruiters: "scoped_out_title_only",
  workday: "held_or_scoped_out",
};

export function diversionKind(source: string): DiversionKind {
  return DIVERSION_BY_SOURCE[source] ?? "scoped_out";
}

/** The prefix `ingest_board` and `ingest_workday_board` tag a swallowed facet failure with. */
const ENRICH_ERROR_PREFIX = "enrich_jobs:";

export type IngestFlowSource = {
  source: string;
  boardsOk: number;
  boardsFailed: number;
  /** Postings the boards handed over, before the scope rules. */
  fetched: number;
  /** Rows written -- `upsert_jobs`' return, so inserts and updates together. */
  stored: number;
  /** `fetched - stored`. Read `diversion` before labelling this. */
  diverted: number;
  /** Postings this board held that it did not see this pass, so now inactive. */
  deactivated: number;
  /** Boards whose postings landed but whose facet write was swallowed. */
  enrichFailed: number;
  diversion: DiversionKind;
};

export type IngestStepId = "boards" | "fetch" | "scope" | "store" | "enrich" | "expire";

export type FlowUnit = "jobs" | "boards";

export type FlowBranch = {
  /** i18n suffix, also the React key. */
  id: "board_error" | "diverted" | "enrich_error";
  value: number;
  unit: FlowUnit;
};

export type IngestFlowStep = {
  id: IngestStepId;
  /** What arrived at this step. */
  value: number;
  unit: FlowUnit;
  /** Where part of it left the pipeline. */
  branches: FlowBranch[];
};

export type IngestFlowLeaf = {
  id: "in_catalog" | "diverted" | "deactivated" | "board_error" | "enrich_error";
  value: number;
  unit: FlowUnit;
  tone: "keep" | "drop" | "error";
};

export type IngestFlow = {
  /** Per provider, biggest fetch first, so the diagram's bands are ordered. */
  sources: IngestFlowSource[];
  steps: IngestFlowStep[];
  leaves: IngestFlowLeaf[];
  totals: {
    boardsOk: number;
    boardsFailed: number;
    fetched: number;
    stored: number;
    diverted: number;
    deactivated: number;
    enrichFailed: number;
  };
  /** The caveats that actually apply to this pass, for the footnote. */
  diversions: DiversionKind[];
  /**
   * Rows with no board: `ingest_catalog` records the pre-loop boilerplate
   * refresh as `source: "boilerplate"`, `board_slug: null` when it fails.
   * A step of the pipeline failed, not a board, so it is reported apart from
   * the board counts rather than inflating them.
   */
  pipelineErrors: { source: string; error: string | null }[];
};

function isEnrichFailure(row: AdminIngestRun): boolean {
  return (row.error_snippet ?? "").startsWith(ENRICH_ERROR_PREFIX);
}

export function buildIngestFlow(rows: AdminIngestRun[]): IngestFlow {
  const bySource = new Map<string, IngestFlowSource>();
  const pipelineErrors: { source: string; error: string | null }[] = [];

  for (const row of rows) {
    // Not a board. Counting it as one would claim a failing board that does
    // not exist, and its ok/upserted counts are always zero anyway.
    if (row.board_slug === null) {
      if (row.status !== "ok") pipelineErrors.push({ source: row.source, error: row.error_snippet });
      continue;
    }
    let entry = bySource.get(row.source);
    if (!entry) {
      entry = {
        source: row.source,
        boardsOk: 0,
        boardsFailed: 0,
        fetched: 0,
        stored: 0,
        diverted: 0,
        deactivated: 0,
        enrichFailed: 0,
        diversion: diversionKind(row.source),
      };
      bySource.set(row.source, entry);
    }
    if (row.status === "ok") {
      entry.boardsOk += 1;
      entry.fetched += row.ok_count;
      entry.stored += row.upserted_count;
      entry.deactivated += row.expired_count;
      // Recorded on an otherwise-ok row on purpose: the facet write is
      // swallowed so a derivation bug cannot cost the posting. Nothing on the
      // page surfaced it before this, which is how it could stay swallowed.
      if (isEnrichFailure(row)) entry.enrichFailed += 1;
    } else {
      entry.boardsFailed += 1;
    }
  }

  const sources = [...bySource.values()]
    .map((entry) => ({ ...entry, diverted: Math.max(entry.fetched - entry.stored, 0) }))
    .sort((a, b) => b.fetched - a.fetched || a.source.localeCompare(b.source));

  const sum = (pick: (entry: IngestFlowSource) => number) =>
    sources.reduce((total, entry) => total + pick(entry), 0);

  const totals = {
    boardsOk: sum((s) => s.boardsOk),
    boardsFailed: sum((s) => s.boardsFailed),
    fetched: sum((s) => s.fetched),
    stored: sum((s) => s.stored),
    diverted: sum((s) => s.diverted),
    deactivated: sum((s) => s.deactivated),
    enrichFailed: sum((s) => s.enrichFailed),
  };

  const steps: IngestFlowStep[] = [
    {
      id: "boards",
      value: totals.boardsOk + totals.boardsFailed,
      unit: "boards",
      branches: [{ id: "board_error", value: totals.boardsFailed, unit: "boards" }],
    },
    { id: "fetch", value: totals.fetched, unit: "jobs", branches: [] },
    {
      id: "scope",
      value: totals.fetched,
      unit: "jobs",
      branches: [{ id: "diverted", value: totals.diverted, unit: "jobs" }],
    },
    { id: "store", value: totals.stored, unit: "jobs", branches: [] },
    {
      id: "enrich",
      value: totals.stored,
      unit: "jobs",
      branches: [{ id: "enrich_error", value: totals.enrichFailed, unit: "boards" }],
    },
    // The one step whose input is not this pass's fetch: `expire_missing` reads
    // what we already hold and deactivates whatever the board stopped listing.
    { id: "expire", value: totals.deactivated, unit: "jobs", branches: [] },
  ];

  const leaves: IngestFlowLeaf[] = [
    { id: "in_catalog", value: totals.stored, unit: "jobs", tone: "keep" },
    { id: "diverted", value: totals.diverted, unit: "jobs", tone: "drop" },
    { id: "deactivated", value: totals.deactivated, unit: "jobs", tone: "drop" },
    { id: "board_error", value: totals.boardsFailed, unit: "boards", tone: "error" },
    { id: "enrich_error", value: totals.enrichFailed, unit: "boards", tone: "error" },
  ];

  const diversions: DiversionKind[] = [];
  for (const entry of sources) {
    // Only when the caveat is actually load-bearing for this pass: a provider
    // that diverted nothing needs no footnote about what its diversion means.
    if (entry.diverted > 0 && !diversions.includes(entry.diversion)) {
      diversions.push(entry.diversion);
    }
  }

  return { sources, steps, leaves, totals, diversions, pipelineErrors };
}

/**
 * Geometry for the provider -> outcome diagram.
 *
 * Plain numbers, computed here rather than in the component, for two reasons:
 * the stacking is the part most likely to be quietly wrong (bands that cross,
 * a stack taller than its box) and is worth testing without a DOM, and React's
 * lint rules forbid the running cursors this needs inside a render.
 *
 * Heights are proportional to `fetched` with `minBand` as a floor: a provider
 * that fetched 3 postings beside one that fetched 9,000 would otherwise be a
 * sub-pixel line, which reads as absent rather than as small.
 */
export type FlowBand = {
  source: string;
  /** Top of the provider's node. */
  y: number;
  height: number;
  storedHeight: number;
  divertedHeight: number;
  /** Top of this provider's slice of each outcome node, so bands never cross. */
  storedAt: number;
  divertedAt: number;
};

export type FlowLayout = {
  bands: FlowBand[];
  /** Total drawing height, padding included. */
  height: number;
  storedTop: number;
  storedHeight: number;
  divertedTop: number;
  divertedHeight: number;
};

export type FlowLayoutOptions = {
  /** Target height of the provider stack, before floors widen it. */
  stack: number;
  gap: number;
  minBand: number;
  padY: number;
};

const EMPTY_LAYOUT: FlowLayout = {
  bands: [],
  height: 0,
  storedTop: 0,
  storedHeight: 0,
  divertedTop: 0,
  divertedHeight: 0,
};

export function flowLayout(
  sources: IngestFlowSource[],
  { stack, gap, minBand, padY }: FlowLayoutOptions,
): FlowLayout {
  const withFetch = sources.filter((entry) => entry.fetched > 0);
  if (withFetch.length === 0) return EMPTY_LAYOUT;

  const gaps = gap * (withFetch.length - 1);
  const floors = withFetch.length * minBand;
  const usable = Math.max(stack - gaps, floors);
  // The proportional part is scaled into what is left after every band has
  // taken its floor, so the floors cannot push the stack past `stack`.
  const flexible = Math.max(usable - floors, 0);
  const fetchedTotal = withFetch.reduce((sum, entry) => sum + entry.fetched, 0);

  const sized = withFetch.map((entry) => {
    const height = minBand + (flexible * entry.fetched) / fetchedTotal;
    const storedHeight = (height * entry.stored) / entry.fetched;
    return {
      source: entry.source,
      height,
      storedHeight,
      divertedHeight: height - storedHeight,
    };
  });

  const leftStack = sized.reduce((sum, band) => sum + band.height, 0) + gaps;
  const storedHeight = sized.reduce((sum, band) => sum + band.storedHeight, 0);
  const divertedHeight = sized.reduce((sum, band) => sum + band.divertedHeight, 0);
  // Only separate the two outcome nodes when there are two of them.
  const outcomeGap = storedHeight > 0 && divertedHeight > 0 ? gap * 2 : 0;
  const rightStack = storedHeight + divertedHeight + outcomeGap;
  const inner = Math.max(leftStack, rightStack);
  const leftTop = padY + (inner - leftStack) / 2;
  const rightTop = padY + (inner - rightStack) / 2;

  let y = leftTop;
  let storedCursor = rightTop;
  let divertedCursor = rightTop + storedHeight + outcomeGap;
  const bands = sized.map((band) => {
    const placed: FlowBand = {
      ...band,
      y,
      storedAt: storedCursor,
      divertedAt: divertedCursor,
    };
    y += band.height + gap;
    storedCursor += band.storedHeight;
    divertedCursor += band.divertedHeight;
    return placed;
  });

  return {
    bands,
    height: inner + padY * 2,
    storedTop: rightTop,
    storedHeight,
    divertedTop: rightTop + storedHeight + outcomeGap,
    divertedHeight,
  };
}
