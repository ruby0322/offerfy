/**
 * Shared intake rules for the `/upload` page's two doors -- choose-a-file /
 * drag-and-drop (`Dropzone.tsx`) and the global paste listener (`page.tsx`).
 *
 * Both doors end up with a `File` and must agree on whether it is acceptable
 * before it reaches `onFile`. Keeping that decision here, free of React and
 * the DOM event types, is what lets this repo's `node:test` runner exercise
 * it directly -- there is no jsdom, Testing Library, vitest or jest here,
 * and this module is deliberately the only unit-testable part of the
 * feature.
 */

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** Kept as an ordered list (a `Set` preserves insertion order) so the
 *  `<input accept>` string Dropzone builds from it is deterministic. */
export const ALLOWED_UPLOAD_EXTENSIONS = new Set([
  "pdf",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "txt",
  "md",
]);

/**
 * Below this many characters, a plain-text paste is treated as not a resume
 * -- a stray Cmd+V of a word or a URL -- rather than silently doing nothing
 * or silently becoming an upload. Only a pasted *string* is subject to this;
 * a pasted *file* (an image or PDF straight off the clipboard) never is, so
 * it is checked with `validateFile`, not `validatePastedText`.
 */
export const MIN_PASTED_TEXT_LENGTH = 200;

/** The filename and MIME type given to a pasted plain-text block so it can
 *  travel through the exact same `onFile` path as a chosen or dropped file.
 *  `.txt` is already in the backend's `ALLOWED_SUFFIXES`
 *  (`apps/backend/app/services/extract.py`), so this needs no backend
 *  change. */
export const PASTED_TEXT_FILENAME = "pasted-resume.txt";
export const PASTED_TEXT_MIME = "text/plain";

export type UploadIntakeRejection = "badType" | "tooLarge" | "tooShort";

export type UploadIntakeResult =
  | { ok: true }
  | { ok: false; reason: UploadIntakeRejection };

/** Lowercased extension with no leading dot, or "" when the name has none. */
export function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return "";
  return filename.slice(dot + 1).toLowerCase();
}

/** Accept/reject a chosen, dropped, or pasted *file* by extension and size.
 *  Takes a minimal shape rather than the DOM `File` type so it is just as
 *  easy to call with a synthesised file in tests. */
export function validateFile(file: { name: string; size: number }): UploadIntakeResult {
  const ext = fileExtension(file.name);
  if (!ALLOWED_UPLOAD_EXTENSIONS.has(ext)) {
    return { ok: false, reason: "badType" };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return { ok: false, reason: "tooLarge" };
  }
  return { ok: true };
}

/** Accept/reject pasted plain text by length, before it is ever turned into
 *  a file. Never applied to a pasted file -- see `validateFile`. */
export function validatePastedText(text: string): UploadIntakeResult {
  if (text.length < MIN_PASTED_TEXT_LENGTH) {
    return { ok: false, reason: "tooShort" };
  }
  return { ok: true };
}

/** Wrap accepted pasted text in a `File` so it can travel through the same
 *  `onFile` path as a chosen or dropped file. Call only after
 *  `validatePastedText` has accepted the text. */
export function synthesizePastedTextFile(text: string): File {
  return new File([text], PASTED_TEXT_FILENAME, { type: PASTED_TEXT_MIME });
}
