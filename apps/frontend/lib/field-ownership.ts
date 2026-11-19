/**
 * Who may write the whole résumé source. The editor shell autosaves the full `source`
 * string on a debounce; while the fields tab owns the source, that PUT would silently
 * overwrite a field edit the server already applied. One owner at a time, so the guard
 * is a single comparison — ask it before any shell save.
 */
export type SaveOwner = "shell" | "fields";

export function mayPutSource(owner: SaveOwner): boolean {
  return owner === "shell";
}

/**
 * Who owns the source right now.
 *
 * The handover, not the steady state, is where this kind of guard leaks. A field edit
 * is a request in flight: the panel can be unmounted (the person switched to the Typst
 * tab the moment they stopped typing) while the server is still applying it, and the
 * shell's buffer is then the *older* document until the response is adopted. So
 * ownership outlives the panel by exactly as long as an edit is outstanding.
 */
export function sourceOwnerFor(state: {
  /** Whether the fields tab is mounted and holding a projection. */
  fieldsOpen: boolean;
  /** Field edits sent and not yet answered (or failed). */
  editsInFlight: number;
}): SaveOwner {
  return state.fieldsOpen || state.editsInFlight > 0 ? "fields" : "shell";
}

/**
 * Whether the shell's debounced autosave has anything to write.
 *
 * `savedSource` is the bytes the server is known to hold -- the response to the last
 * PUT, or the source a `/edit` returned. It is only ever set from such a response, so
 * a mismatch means "may be unsaved" (harmless: we save) and never "already saved" when
 * it is not. Without it, releasing ownership back to the shell schedules a PUT of the
 * bytes `/edit` just returned: identical content, but a wasted write and one more way
 * for a stale buffer to reach the server at the one moment it matters.
 */
export function shellShouldPutSource(state: {
  owner: SaveOwner;
  source: string;
  savedSource: string | null;
}): boolean {
  return mayPutSource(state.owner) && state.source !== state.savedSource;
}

/**
 * Whether a failed field edit leaves the shell's buffer describing a document the
 * server no longer has, so it must be re-read.
 *
 * The panel refetching its own projection is not enough. `source` and `savedSource`
 * stay as they were, the autosave stays quiet while the fields tab owns them, and the
 * panel self-heals on the next field edit -- but switch to the Typst tab and the
 * **pre-edit** document is on screen, and the first keystroke there arms a PUT of it.
 * `PUT /v1/resumes/{id}` carries no base hash and sets the source unconditionally, so
 * an applied edit is gone with nothing refused anywhere.
 *
 * Two failures leave it that way. A timeout: the shell stopped waiting, and whether the
 * server applied the edit is unknowable from the client, so the buffer may be a document
 * behind. And a staleness refusal from a writer outside this page: the server's source
 * moved and nothing here moved with it. A refusal that changed nothing -- a failed
 * compile, an intent the backend does not support -- leaves the buffer correct, and a
 * staleness refusal caused by *this* page (the Typst tab's own autosave, a chat turn, a
 * restore) already moved the buffer as part of writing.
 */
export function shellNeedsResync(failure: {
  /** The request was aborted: the shell gave up waiting on it. */
  aborted: boolean;
  /** `/edit`'s refusal code, or null when the failure was not a refusal. */
  code: string | null;
  /** Whether the write that made this stale came from this page. */
  writtenHere: boolean;
}): boolean {
  if (failure.aborted) return true;
  if (failure.code === "stale_hash" || failure.code === "expect_mismatch") {
    return !failure.writtenHere;
  }
  return false;
}
