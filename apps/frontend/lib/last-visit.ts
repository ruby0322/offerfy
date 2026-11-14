const KEY = "offerfy:dashboard:last-visit";

/**
 * When this browser last opened the dashboard, for the "since Tuesday" line.
 *
 * Deliberately per-browser rather than a column on the user. It is a reading
 * aid, not a fact worth storing: it should say "since you last looked here",
 * and someone who opens the dashboard on their phone has not seen what their
 * laptop showed them. A server-side marker would also have to decide what
 * counts as a visit, which is exactly the ambiguity the digest's `opened_at`
 * already documents.
 *
 * Every access is wrapped: storage throws in a private window and comes back
 * empty when site data is cleared, and the line it feeds has to be absent in
 * that case rather than the page failing.
 */
export function readLastVisit(): string | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    // A corrupt value would otherwise be sent to the API as a `since`.
    return Number.isNaN(Date.parse(raw)) ? null : raw;
  } catch {
    return null;
  }
}

export function writeLastVisit(at: Date = new Date()): void {
  try {
    window.localStorage.setItem(KEY, at.toISOString());
  } catch {
    /* no storage; the line simply never appears */
  }
}
