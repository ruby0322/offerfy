import assert from "node:assert/strict";
import test from "node:test";
import { mergeKnownKeys } from "./agent-keys";

const key = (id: string, revoked_at: string | null = null) => ({ id, revoked_at });

test("a mint the server has not caught up with is kept", () => {
  // The case that shipped broken: the list read that follows a mint was
  // answered from before the insert, and assigning it dropped the row the
  // card had just built from the mint's own response.
  const merged = mergeKnownKeys([key("old")], [key("old"), key("fresh")]);
  assert.deepEqual(
    merged.map((k) => k.id),
    ["old", "fresh"],
  );
});

test("a mint is kept even when the stale answer is empty", () => {
  const merged = mergeKnownKeys([], [key("fresh")]);
  assert.deepEqual(
    merged.map((k) => k.id),
    ["fresh"],
  );
});

test("a revoke the server has not caught up with stays revoked", () => {
  const merged = mergeKnownKeys([key("a")], [key("a", "2026-11-22T00:00:00Z")]);
  assert.equal(merged[0].revoked_at, "2026-11-22T00:00:00Z");
});

test("the server's own revocation wins over a row this page thinks is live", () => {
  // Revoked in another tab: the server knows something this page does not,
  // and the merge must not argue with it.
  const merged = mergeKnownKeys([key("a", "2026-11-22T09:00:00Z")], [key("a")]);
  assert.equal(merged[0].revoked_at, "2026-11-22T09:00:00Z");
});

test("the server decides the order, and rows it omits go last", () => {
  const merged = mergeKnownKeys(
    [key("second"), key("first")],
    [key("first"), key("second"), key("unseen")],
  );
  assert.deepEqual(
    merged.map((k) => k.id),
    ["second", "first", "unseen"],
  );
});

test("knowing nothing returns the server's list untouched", () => {
  const server = [key("a"), key("b")];
  assert.equal(mergeKnownKeys(server, []), server);
});

test("extra fields on a row survive the merge", () => {
  type Row = { id: string; revoked_at: string | null; label: string };
  const server: Row[] = [{ id: "a", revoked_at: null, label: "laptop" }];
  const known: Row[] = [{ id: "a", revoked_at: "2026-11-22T00:00:00Z", label: "laptop" }];
  assert.deepEqual(mergeKnownKeys(server, known), [
    { id: "a", revoked_at: "2026-11-22T00:00:00Z", label: "laptop" },
  ]);
});
