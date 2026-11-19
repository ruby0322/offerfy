// The editor's remembered left tab ("chat" | "typst" | ...), per resume id.
// sessionStorage, not localStorage: a same-session reload keeps the tab the
// user was on, but a brand new session falls back to the default rather than
// an arbitrarily old choice. Exported so a page that navigates *into* the
// editor (e.g. upload) can set the tab before the editor ever mounts, which
// is what lets it land there with no flash of the default tab first.
const editorTabKey = (id: string) => `offerfy:editor-tab:${id}`;

/** Every tab the editor's left panel has, in the order the strip shows them. */
export const EDITOR_TABS = [
  "typst",
  "fields",
  "chat",
  "template",
  "settings",
  "history",
] as const;

export type EditorTab = (typeof EDITOR_TABS)[number];

export const DEFAULT_EDITOR_TAB: EditorTab = "typst";

/**
 * A stored or restored tab name, as a tab that exists.
 *
 * sessionStorage outlives a deploy: it holds "preview" from before the preview stopped
 * being a tab, and it will hold "fields" if this build is ever rolled back. An unknown
 * value selects no trigger at all and the panel renders empty, so it falls back here
 * instead.
 */
export function normalizeEditorTab(stored: string | null | undefined): EditorTab {
  const known = EDITOR_TABS.find((tab) => tab === stored);
  return known ?? DEFAULT_EDITOR_TAB;
}

export function loadEditorTab(id: string): string | null {
  try {
    return sessionStorage.getItem(editorTabKey(id));
  } catch {
    return null;
  }
}

export function saveEditorTab(id: string, tab: string) {
  try {
    sessionStorage.setItem(editorTabKey(id), tab);
  } catch {
    /* storage can be unavailable in private mode */
  }
}
