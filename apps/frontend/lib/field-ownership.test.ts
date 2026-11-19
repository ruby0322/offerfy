import assert from "node:assert/strict";
import test from "node:test";

import {
  mayPutSource,
  shellNeedsResync,
  shellShouldPutSource,
  sourceOwnerFor,
} from "./field-ownership";

test("the shell may not PUT source while the fields tab owns it", () => {
  assert.equal(mayPutSource("fields"), false);
  assert.equal(mayPutSource("shell"), true);
});

test("the fields tab keeps the source until its last edit is answered", () => {
  assert.equal(sourceOwnerFor({ fieldsOpen: true, editsInFlight: 0 }), "fields");
  assert.equal(sourceOwnerFor({ fieldsOpen: true, editsInFlight: 2 }), "fields");
  // The panel is gone -- the person switched tabs the moment they stopped typing --
  // but the edit it sent is still being applied, so the shell's buffer is the older
  // document and must not be written back.
  assert.equal(sourceOwnerFor({ fieldsOpen: false, editsInFlight: 1 }), "fields");
  assert.equal(sourceOwnerFor({ fieldsOpen: false, editsInFlight: 0 }), "shell");
});

test("the shell does not re-PUT the bytes the server just handed it", () => {
  const source = "= CV\n";
  assert.equal(shellShouldPutSource({ owner: "shell", source, savedSource: source }), false);
  assert.equal(shellShouldPutSource({ owner: "shell", source, savedSource: "old" }), true);
  // Nothing known about the server's copy yet: save, which is the safe direction.
  assert.equal(shellShouldPutSource({ owner: "shell", source, savedSource: null }), true);
  // Ownership still wins over a dirty buffer.
  assert.equal(shellShouldPutSource({ owner: "fields", source, savedSource: "old" }), false);
});

test("a timed-out or foreign edit sends the shell back to the server", () => {
  const f = (over: Partial<Parameters<typeof shellNeedsResync>[0]>) =>
    shellNeedsResync({ aborted: false, code: null, writtenHere: false, ...over });

  // The shell stopped waiting. Whether the edit landed is unknowable here, so the
  // buffer may be a document behind -- and the Typst tab's first keystroke would PUT
  // it back over the edit that did land, with no base hash to refuse it.
  assert.equal(f({ aborted: true }), true);
  assert.equal(f({ aborted: true, writtenHere: true }), true);

  // Someone outside this page wrote the résumé: the server's source moved and nothing
  // here moved with it.
  assert.equal(f({ code: "stale_hash" }), true);
  assert.equal(f({ code: "expect_mismatch" }), true);

  // ...but this page's own writer (the Typst tab's autosave, a chat turn, a restore)
  // moved the buffer as part of writing, so it is already current.
  assert.equal(f({ code: "stale_hash", writtenHere: true }), false);
  assert.equal(f({ code: "expect_mismatch", writtenHere: true }), false);

  // A refusal that changed nothing leaves the buffer correct; re-reading would only
  // cost a request, and adopting mid-edit is the thing this file exists to avoid.
  assert.equal(f({ code: "compile_failed" }), false);
  assert.equal(f({ code: "unsupported" }), false);
  assert.equal(f({ code: null }), false);
});
