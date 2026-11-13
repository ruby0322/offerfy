import assert from "node:assert/strict";
import test from "node:test";
import { buildIngestFlow, diversionKind, flowLayout } from "./admin-ingest-flow";
import type { AdminIngestRun } from "./api";

/** Mirrors the constants IngestFlow draws with. */
const LAYOUT = { stack: 236, gap: 10, minBand: 12, padY: 16 } as const;

const row = (over: Partial<AdminIngestRun> = {}): AdminIngestRun => ({
  id: "row-1",
  source: "greenhouse",
  board_slug: "anthropic",
  started_at: "2026-11-13T03:00:00.000Z",
  finished_at: "2026-11-13T03:00:02.000Z",
  status: "ok",
  ok_count: 0,
  error_count: 0,
  upserted_count: 0,
  expired_count: 0,
  error_snippet: null,
  ...over,
});

test("folds the per-board rows of a pass into one entry per provider", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "a", ok_count: 100, upserted_count: 60, expired_count: 4 }),
    row({ board_slug: "b", ok_count: 50, upserted_count: 20, expired_count: 1 }),
    row({ source: "lever", board_slug: "c", ok_count: 10, upserted_count: 7 }),
  ]);

  assert.deepEqual(
    flow.sources.map((entry) => [entry.source, entry.fetched, entry.stored, entry.diverted]),
    [
      ["greenhouse", 150, 80, 70],
      ["lever", 10, 7, 3],
    ],
  );
  assert.equal(flow.totals.boardsOk, 3);
  assert.equal(flow.totals.deactivated, 5);
  assert.equal(flow.totals.fetched, 160);
  // The invariant the diagram is drawn from: nothing fetched vanishes.
  assert.equal(flow.totals.stored + flow.totals.diverted, flow.totals.fetched);
});

test("a failed board is counted as a board and contributes no postings", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "live", ok_count: 40, upserted_count: 30 }),
    row({ board_slug: "dead", status: "error", error_count: 1, error_snippet: "board not found" }),
  ]);

  const [greenhouse] = flow.sources;
  assert.equal(greenhouse.boardsOk, 1);
  assert.equal(greenhouse.boardsFailed, 1);
  assert.equal(greenhouse.fetched, 40);
  assert.equal(flow.steps.find((step) => step.id === "boards")?.value, 2);
  assert.deepEqual(flow.steps.find((step) => step.id === "boards")?.branches, [
    { id: "board_error", value: 1, unit: "boards" },
  ]);
});

test("a row with no board is a failed pipeline step, not a failed board", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "live", ok_count: 5, upserted_count: 5 }),
    row({
      source: "boilerplate",
      board_slug: null,
      status: "error",
      error_count: 1,
      error_snippet: "refresh_boilerplate: boom",
    }),
  ]);

  assert.equal(flow.totals.boardsFailed, 0);
  assert.equal(flow.sources.length, 1);
  assert.deepEqual(flow.pipelineErrors, [
    { source: "boilerplate", error: "refresh_boilerplate: boom" },
  ]);
});

test("a swallowed facet failure is read off an otherwise-ok row", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "a", ok_count: 9, upserted_count: 9, error_snippet: "enrich_jobs: nope" }),
    row({ board_slug: "b", ok_count: 9, upserted_count: 9 }),
  ]);

  assert.equal(flow.totals.enrichFailed, 1);
  assert.equal(flow.totals.boardsFailed, 0);
  assert.deepEqual(flow.steps.find((step) => step.id === "enrich")?.branches, [
    { id: "enrich_error", value: 1, unit: "boards" },
  ]);
});

test("what an unstored posting means is per provider, and workday is not out of scope", () => {
  assert.equal(diversionKind("greenhouse"), "scoped_out");
  assert.equal(diversionKind("ashby"), "scoped_out");
  assert.equal(diversionKind("smartrecruiters"), "scoped_out_title_only");
  // The one that matters: ~12k already-held postings count as fetched and are
  // never upserted, so labelling this difference "out of scope" is wrong.
  assert.equal(diversionKind("workday"), "held_or_scoped_out");
  // A provider we have not met yet gets the plain reading rather than throwing.
  assert.equal(diversionKind("teamtailor"), "scoped_out");
});

test("only the caveats that bear on this pass are reported", () => {
  const nothingDiverted = buildIngestFlow([
    row({ source: "workday", board_slug: "nvidia", ok_count: 20, upserted_count: 20 }),
  ]);
  assert.deepEqual(nothingDiverted.diversions, []);

  const mixed = buildIngestFlow([
    row({ board_slug: "a", ok_count: 100, upserted_count: 40 }),
    row({ source: "workday", board_slug: "nvidia", ok_count: 1200, upserted_count: 12 }),
    row({ source: "lever", board_slug: "c", ok_count: 30, upserted_count: 10 }),
  ]);
  assert.deepEqual(mixed.diversions, ["held_or_scoped_out", "scoped_out"]);
});

test("a pass that stored everything diverts nothing, never a negative", () => {
  const flow = buildIngestFlow([row({ board_slug: "a", ok_count: 12, upserted_count: 12 })]);
  assert.equal(flow.totals.diverted, 0);
  assert.equal(flow.leaves.find((leaf) => leaf.id === "diverted")?.value, 0);
});

test("an empty pass yields zeros rather than NaN", () => {
  const flow = buildIngestFlow([]);
  assert.deepEqual(flow.sources, []);
  assert.equal(flow.totals.fetched, 0);
  assert.ok(flow.steps.every((step) => step.value === 0));
  assert.deepEqual(flowLayout(flow.sources, LAYOUT).bands, []);
  assert.equal(flowLayout(flow.sources, LAYOUT).height, 0);
});

test("bands are proportional to fetched, floored, and fit inside the drawing", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "big", ok_count: 9000, upserted_count: 6000 }),
    row({ source: "lever", board_slug: "tiny", ok_count: 3, upserted_count: 3 }),
  ]);
  const { bands, height } = flowLayout(flow.sources, LAYOUT);

  assert.equal(bands.length, 2);
  // The 3-posting provider stays visible instead of collapsing to a hairline.
  assert.ok(bands[1].height >= LAYOUT.minBand, `tiny band was ${bands[1].height}`);
  assert.ok(bands[0].height > bands[1].height);
  for (const band of bands) {
    assert.ok(band.y >= LAYOUT.padY - 0.001, `band started at ${band.y}`);
    assert.ok(band.y + band.height <= height - LAYOUT.padY + 0.001);
    assert.ok(Math.abs(band.storedHeight + band.divertedHeight - band.height) < 0.001);
  }
  // Fully stored provider: nothing to draw on the diverted side.
  assert.equal(bands[1].divertedHeight, 0);
});

test("each provider's slice of an outcome sits in band order, so bands cannot cross", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "big", ok_count: 900, upserted_count: 400 }),
    row({ source: "lever", board_slug: "mid", ok_count: 300, upserted_count: 100 }),
    row({ source: "ashby", board_slug: "small", ok_count: 90, upserted_count: 30 }),
  ]);
  const layout = flowLayout(flow.sources, LAYOUT);

  const stored = layout.bands.map((band) => band.storedAt);
  const diverted = layout.bands.map((band) => band.divertedAt);
  assert.deepEqual([...stored].sort((a, b) => a - b), stored);
  assert.deepEqual([...diverted].sort((a, b) => a - b), diverted);

  // Every slice lands inside its own outcome node, and together they fill it.
  const last = layout.bands[layout.bands.length - 1];
  assert.equal(layout.bands[0].storedAt, layout.storedTop);
  assert.equal(layout.bands[0].divertedAt, layout.divertedTop);
  assert.ok(
    Math.abs(last.storedAt + last.storedHeight - (layout.storedTop + layout.storedHeight)) < 0.001,
  );
  assert.ok(
    Math.abs(
      last.divertedAt + last.divertedHeight - (layout.divertedTop + layout.divertedHeight),
    ) < 0.001,
  );
  // The two outcome nodes do not overlap.
  assert.ok(layout.divertedTop >= layout.storedTop + layout.storedHeight);
});

test("one outcome only means no gap is reserved for the other", () => {
  const allStored = flowLayout(
    buildIngestFlow([row({ board_slug: "a", ok_count: 40, upserted_count: 40 })]).sources,
    LAYOUT,
  );
  assert.equal(allStored.divertedHeight, 0);
  assert.equal(allStored.divertedTop, allStored.storedTop + allStored.storedHeight);
});

test("a provider that fetched nothing gets no band", () => {
  const flow = buildIngestFlow([
    row({ board_slug: "a", ok_count: 10, upserted_count: 10 }),
    row({ source: "ashby", board_slug: "dead", status: "error", error_count: 1 }),
  ]);
  assert.deepEqual(
    flowLayout(flow.sources, LAYOUT).bands.map((band) => band.source),
    ["greenhouse"],
  );
});
