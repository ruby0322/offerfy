import assert from "node:assert/strict";
import test from "node:test";

import {
  ALLOWED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  MIN_PASTED_TEXT_LENGTH,
  PASTED_TEXT_FILENAME,
  PASTED_TEXT_MIME,
  fileExtension,
  synthesizePastedTextFile,
  validateFile,
  validatePastedText,
} from "./upload-intake";

test("every allowed extension is accepted", () => {
  for (const ext of ALLOWED_UPLOAD_EXTENSIONS) {
    const result = validateFile({ name: `resume.${ext}`, size: 1024 });
    assert.deepEqual(result, { ok: true });
  }
});

test("a disallowed extension is rejected", () => {
  const result = validateFile({ name: "resume.docx", size: 1024 });
  assert.deepEqual(result, { ok: false, reason: "badType" });
});

test("a file over the size limit is rejected even with an allowed extension", () => {
  const result = validateFile({ name: "resume.pdf", size: MAX_UPLOAD_BYTES + 1 });
  assert.deepEqual(result, { ok: false, reason: "tooLarge" });
});

test("a file exactly at the size limit is accepted", () => {
  const result = validateFile({ name: "resume.pdf", size: MAX_UPLOAD_BYTES });
  assert.deepEqual(result, { ok: true });
});

test("extension matching is case-insensitive and ignores query-like suffixes in the name", () => {
  assert.equal(fileExtension("Resume.PDF"), "pdf");
  assert.equal(fileExtension("photo.JPEG"), "jpeg");
  assert.equal(fileExtension("noextension"), "");
});

test("pasted text just under the minimum length is rejected", () => {
  const text = "a".repeat(MIN_PASTED_TEXT_LENGTH - 1);
  assert.equal(text.length, 199);
  const result = validatePastedText(text);
  assert.deepEqual(result, { ok: false, reason: "tooShort" });
});

test("pasted text exactly at the minimum length is accepted", () => {
  const text = "a".repeat(MIN_PASTED_TEXT_LENGTH);
  assert.equal(text.length, 200);
  const result = validatePastedText(text);
  assert.deepEqual(result, { ok: true });
});

test("synthesising a pasted-text file uses the fixed filename and MIME type", () => {
  const file = synthesizePastedTextFile("a".repeat(MIN_PASTED_TEXT_LENGTH));
  assert.equal(file.name, PASTED_TEXT_FILENAME);
  assert.equal(file.name, "pasted-resume.txt");
  assert.equal(file.type, PASTED_TEXT_MIME);
  assert.equal(file.type, "text/plain");
});

test("a pasted file is validated like any other file, not by the paste-text length rule", () => {
  // A ten-byte pasted image is far shorter than MIN_PASTED_TEXT_LENGTH, but
  // validateFile knows nothing about character counts -- only extension and
  // byte size -- so it must pass.
  const result = validateFile({ name: "screenshot.png", size: 10 });
  assert.deepEqual(result, { ok: true });
});
