import assert from "node:assert/strict";
import test from "node:test";

import { createEditQueue } from "./edit-queue";

type P = { hash: string };
type I = { hash: string; unit: string };
type R = { projection: P; typstSource: string };

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A queue over a fake `/edit`: every answer carries a projection with a new hash. */
function harness(options: { fails?: (intent: I) => Error | null } = {}) {
  const sent: I[] = [];
  let started = 0;
  let settled = 0;
  const send = async (intent: I): Promise<R> => {
    sent.push(intent);
    // `/edit` compiles before it writes: an answer is never the same turn as the ask.
    await tick();
    const failure = options.fails?.(intent);
    if (failure) throw failure;
    return { projection: { hash: `after(${intent.hash})` }, typstSource: `src(${intent.hash})` };
  };
  const queue = createEditQueue<P, I, R>(send, {
    onStart: () => {
      started += 1;
    },
    onSettle: () => {
      settled += 1;
    },
  });
  /** What a commit for `unit` would be, against whatever projection the queue holds. */
  const intentFor = (unit: string) => (p: P): I => ({ hash: p.hash, unit });
  return {
    queue,
    sent,
    hashes: () => sent.map((intent) => intent.hash),
    counts: () => ({ started, settled }),
    intentFor,
  };
}

test("the second commit is built from the answer to the first", () => {
  // The whole point of the queue. `/edit` re-checks the hash before it compiles, so a
  // second commit still carrying the first's hash is refused outright -- and what gets
  // refused is whatever the person typed second.
  const h = harness();
  h.queue.publish({ hash: "P0" });
  const a = h.queue.push(h.intentFor("uA"));
  const b = h.queue.push(h.intentFor("uB"));
  return Promise.all([a, b]).then(() => {
    assert.deepEqual(h.hashes(), ["P0", "after(P0)"]);
    assert.deepEqual(
      h.sent.map((intent) => intent.unit),
      ["uA", "uB"],
    );
  });
});

test("a four-field burst sends every commit, each on the answer before it", async () => {
  // uA uB uC uD typed inside one compile window. Every one is sent, and no two carry
  // the same hash -- the shape that made every second one a 409.
  const h = harness();
  h.queue.publish({ hash: "P0" });
  const all = ["uA", "uB", "uC", "uD"].map((unit) => h.queue.push(h.intentFor(unit)));
  const results = await Promise.all(all);
  assert.deepEqual(h.hashes(), ["P0", "after(P0)", "after(after(P0))", "after(after(after(P0)))"]);
  assert.equal(new Set(h.hashes()).size, 4, "two commits carried the same hash");
  assert.deepEqual(
    results.map((out) => out?.typstSource),
    ["src(P0)", "src(after(P0))", "src(after(after(P0)))", "src(after(after(after(P0))))"],
  );
});

test("the next commit does not wait for the caller's continuation", async () => {
  // The defect this replaced: the queue's tail resolved a microtask *before* the
  // caller's `await` continuation, and the caller was the only thing that refreshed the
  // projection -- so the next build read one that was two answers old. The queue
  // publishes the answer itself, so a caller that takes its time cannot affect what the
  // next commit is built from.
  const h = harness();
  h.queue.publish({ hash: "P0" });
  const slow = h.queue.push(h.intentFor("uA")).then(async () => {
    for (let i = 0; i < 5; i++) await tick();
  });
  const b = h.queue.push(h.intentFor("uB"));
  await Promise.all([slow, b]);
  assert.deepEqual(h.hashes(), ["P0", "after(P0)"]);
});

test("a commit the answer makes redundant is dropped, and does not block the queue", async () => {
  // A blur and an unmount cleanup hand over the same text. The second build sees the
  // projection the first's answer carried, finds nothing to do, and costs no request.
  const h = harness();
  h.queue.publish({ hash: "P0" });
  const first = h.queue.push(h.intentFor("uA"));
  const duplicate = h.queue.push((p) => (p.hash === "after(P0)" ? null : h.intentFor("uA")(p)));
  const third = h.queue.push(h.intentFor("uB"));
  const [a, dup, c] = await Promise.all([first, duplicate, third]);
  assert.ok(a, "the first commit was sent");
  assert.equal(dup, null, "the redundant commit resolved as nothing sent");
  assert.ok(c, "the queue carried on after it");
  assert.deepEqual(h.hashes(), ["P0", "after(P0)"]);
  // Nothing sent still counts as a commit started and settled: ownership covers the
  // whole queued span, including the entries that turn out to be no-ops.
  assert.deepEqual(h.counts(), { started: 3, settled: 3 });
});

test("a failure reaches its own caller and does not wedge the queue", async () => {
  const boom = new Error("compile_failed");
  const h = harness({ fails: (intent) => (intent.unit === "uA" ? boom : null) });
  h.queue.publish({ hash: "P0" });
  const a = h.queue.push(h.intentFor("uA"));
  const b = h.queue.push(h.intentFor("uB"));
  await assert.rejects(() => a, /compile_failed/);
  assert.ok(await b, "the commit behind a failure must still be sent");
  // The refused commit published nothing, so the next one carries the hash the server
  // still holds.
  assert.deepEqual(h.hashes(), ["P0", "P0"]);
  assert.deepEqual(h.counts(), { started: 2, settled: 2 });
});

test("ownership is raised before the first await and dropped when that commit settles", async () => {
  const h = harness();
  h.queue.publish({ hash: "P0" });
  const pending = h.queue.push(h.intentFor("uA"));
  // Synchronously after `push`: a save path running later in the same event has to see
  // this already up.
  assert.deepEqual(h.counts(), { started: 1, settled: 0 });
  await pending;
  assert.deepEqual(h.counts(), { started: 1, settled: 1 });
});

test("nothing is sent before a projection has been published", async () => {
  const h = harness();
  assert.equal(await h.queue.push(h.intentFor("uA")), null);
  assert.deepEqual(h.hashes(), []);
  // And once one is published, the same push works.
  h.queue.publish({ hash: "P0" });
  assert.ok(await h.queue.push(h.intentFor("uA")));
  assert.deepEqual(h.hashes(), ["P0"]);
});

test("a projection published from outside is what the next commit uses", async () => {
  // The panel's own fetch -- at mount, and after a `stale_hash` reload.
  const h = harness();
  h.queue.publish({ hash: "P0" });
  h.queue.publish({ hash: "P-reloaded" });
  await h.queue.push(h.intentFor("uA"));
  assert.deepEqual(h.hashes(), ["P-reloaded"]);
});
