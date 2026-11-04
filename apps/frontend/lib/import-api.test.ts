import assert from "node:assert/strict";
import test from "node:test";
import type { ImportSnapshot } from "./import-progress";
import { streamImportFromBody } from "./api";

function snapshot(version: number): ImportSnapshot {
  return {
    import_id: "11111111-1111-1111-1111-111111111111",
    status: version < 3 ? "running" : "done",
    version,
    applied: version,
    source_hash: `h${version}`,
    steps: [
      { key: "summary", label: "Summary", status: "done", detail: null },
    ],
    error: null,
    started_at: "2026-11-04T00:00:00Z",
    stale: false,
  };
}

function sseFrame(snap: ImportSnapshot): string {
  return `data: ${JSON.stringify(snap)}\n\n`;
}

/** A ReadableStream<Uint8Array> that yields exactly the given byte chunks, one per read(). */
function bodyFromChunks(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(chunks[i]);
        i += 1;
      } else {
        controller.close();
      }
    },
  });
}

test("reassembles one sse frame split across two reads", async () => {
  const encoder = new TextEncoder();
  const whole = sseFrame(snapshot(1));
  // Split mid-frame so neither chunk alone is a parseable block.
  const splitAt = Math.floor(whole.length / 2);
  const chunks = [
    encoder.encode(whole.slice(0, splitAt)),
    encoder.encode(whole.slice(splitAt)),
  ];

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks(chunks), (s) => {
    seen.push(s);
  });

  assert.deepEqual(seen.map((s) => s.version), [1]);
});

test("parses two sse frames concatenated into a single chunk", async () => {
  const encoder = new TextEncoder();
  const both = sseFrame(snapshot(2)) + sseFrame(snapshot(3));
  const chunks = [encoder.encode(both)];

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks(chunks), (s) => {
    seen.push(s);
  });

  assert.deepEqual(seen.map((s) => s.version), [2, 3]);
});

test("parses concatenated and split sse frames into snapshots, in order", async () => {
  const encoder = new TextEncoder();
  const frame1 = sseFrame(snapshot(1));
  const frame2and3 = sseFrame(snapshot(2)) + sseFrame(snapshot(3));
  const splitAt = Math.floor(frame1.length / 2);
  const chunks = [
    encoder.encode(frame1.slice(0, splitAt)),
    encoder.encode(frame1.slice(splitAt) + frame2and3),
  ];

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks(chunks), (s) => {
    seen.push(s);
  });

  assert.deepEqual(seen.map((s) => s.version), [1, 2, 3]);
});

test("ignores non-data lines and blank keep-alive blocks", async () => {
  const encoder = new TextEncoder();
  const text = `: keep-alive\n\n${sseFrame(snapshot(1))}`;
  const chunks = [encoder.encode(text)];

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks(chunks), (s) => {
    seen.push(s);
  });

  assert.deepEqual(seen.map((s) => s.version), [1]);
});

test("carries the source hash through, and survives an api that omits it", async () => {
  // The field the preview refresh follows (see `importSourceChanged`), and
  // the deploy window where a new page is served frames by an older API:
  // dropping the whole frame there would take the progress card with it.
  const encoder = new TextEncoder();
  const older: Record<string, unknown> = { ...snapshot(1) };
  delete older.source_hash;
  const text = sseFrame(snapshot(2)) + `data: ${JSON.stringify(older)}\n\n`;

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks([encoder.encode(text)]), (s) => {
    seen.push(s);
  });

  assert.deepEqual(
    seen.map((s) => s.source_hash),
    ["h2", undefined],
  );
});

test("a source hash of the wrong type rejects the frame", async () => {
  const encoder = new TextEncoder();
  const bad = { ...snapshot(1), source_hash: 7 };
  const text = `data: ${JSON.stringify(bad)}\n\n` + sseFrame(snapshot(2));

  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(bodyFromChunks([encoder.encode(text)]), (s) => {
    seen.push(s);
  });

  assert.deepEqual(seen.map((s) => s.version), [2]);
});
