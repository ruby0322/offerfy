/**
 * Reconciling the agent-key list with what this page already knows.
 *
 * A write and the re-read that follows it can disagree, and the write is the
 * one telling the truth. `app/db.py::get_db` commits in FastAPI's dependency
 * teardown, and FastAPI closes that exit stack *after* the response has been
 * sent (`request_response` in fastapi/routing.py awaits the response inside
 * the stack), so a list read issued the instant a mint returns can be answered
 * from before the insert. Measured against prod on 2026-11-22: ~360ms between
 * the 200 that carried a new token and the row becoming readable. The card
 * replaced its own correct state with that stale answer, so a key minted
 * seconds earlier rendered as "no keys yet" until the page was reloaded.
 *
 * `app/routers/account.py` now commits before responding, which closes that
 * window at the source. This merge stays because it is the honest rule for a
 * list that is re-read after a change: a response cannot be allowed to
 * un-create a credential the person is looking at, whatever made it late --
 * the same reasoning that already builds the row optimistically from the mint.
 */

/** The fields the merge needs. `AgentKey` in lib/api.ts satisfies it. */
export type KnownKey = {
  id: string;
  revoked_at: string | null;
};

/**
 * The server's list, corrected by what this page did and the server has not
 * caught up with yet.
 *
 * Two corrections, both in the direction of "a change that already succeeded
 * stays visible":
 *   - a row the server omits entirely is kept (a mint it has not seen);
 *   - a row the server still calls live is marked revoked when this page
 *     revoked it (a revoke it has not seen).
 *
 * Everything else is the server's to decide, order included -- a key revoked
 * in another tab arrives revoked here, and is left that way.
 *
 * One wart, deliberately left: a locally known row the server has dropped for
 * good is kept until the next reload. The server lists every live key and the
 * ten most recently retired, so reaching that needs ten retirements inside one
 * page view; preferring the stale row over blinking a real credential off the
 * screen is the right trade at that frequency.
 */
export function mergeKnownKeys<T extends KnownKey>(server: T[], known: T[]): T[] {
  if (known.length === 0) return server;
  const knownById = new Map(known.map((key) => [key.id, key]));
  const merged = server.map((row) => {
    const mine = knownById.get(row.id);
    if (mine && mine.revoked_at && !row.revoked_at) {
      return { ...row, revoked_at: mine.revoked_at };
    }
    return row;
  });
  const served = new Set(server.map((row) => row.id));
  return merged.concat(known.filter((row) => !served.has(row.id)));
}
