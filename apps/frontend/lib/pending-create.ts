// A just-created resume hands itself off to the editor the same way an upload
// does, so the editor can open on chat instead of raw Typst markup. Module
// state, not sessionStorage: this is a same-navigation handoff, and a reload
// should fall through to whatever tab the user themselves last chose.
const pendingCreates = new Set<string>();

export function stashPendingCreate(resumeId: string): void {
  pendingCreates.add(resumeId);
}

export function peekPendingCreate(resumeId: string): boolean {
  return pendingCreates.has(resumeId);
}

export function takePendingCreate(resumeId: string): boolean {
  return pendingCreates.delete(resumeId);
}
