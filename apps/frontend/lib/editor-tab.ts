// The editor's remembered left tab ("chat" | "typst" | ...), per resume id.
// sessionStorage, not localStorage: a same-session reload keeps the tab the
// user was on, but a brand new session falls back to the default rather than
// an arbitrarily old choice. Exported so a page that navigates *into* the
// editor (e.g. upload) can set the tab before the editor ever mounts, which
// is what lets it land there with no flash of the default tab first.
const editorTabKey = (id: string) => `offerfy:editor-tab:${id}`;

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
