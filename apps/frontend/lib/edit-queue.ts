/**
 * One field commit at a time, each built from the answer to the one before it.
 *
 * `/edit` re-checks the request's hash *before* it compiles and again after, so two
 * commits in flight at once mean the second is refused -- and the one refused is
 * whatever the person typed second, which by then may have no surface left to re-send
 * it. So commits are serialised, and the intent is **built when its turn comes**, from
 * the projection the previous answer carried.
 *
 * The projection lives here, in the queue, and is published by `push` itself the moment
 * an answer arrives. That is the whole point of this module: a consumer's `await push()`
 * continuation runs a microtask *after* the queue's tail resolves, so a queue that left
 * publishing to its caller would hand the next build a projection two answers old. The
 * ordering is the risky part of the fields tab and it is pure, so it is tested here
 * rather than read.
 *
 * Generic over the projection, intent and answer so this module imports nothing: it is
 * the mechanism, not the editor.
 */

export type EditQueue<P, I, R> = {
  /**
   * Queue one commit. `build` is called when its turn comes, with the projection as it
   * is *then*; returning null means there is nothing left to send. The promise answers
   * with the result, or null when nothing was sent, and rejects with whatever `send`
   * rejected with.
   */
  push: (build: (projection: P) => I | null) => Promise<R | null>;
  /** Hand the queue a projection from outside it -- a fetch, or a reload. */
  publish: (projection: P) => void;
  /** The projection the next build would be given, or null if there is none yet. */
  current: () => P | null;
};

export function createEditQueue<P, I, R extends { projection: P }>(
  send: (intent: I) => Promise<R>,
  hooks: {
    /** Called synchronously by `push`, before anything is awaited. */
    onStart?: () => void;
    /** Called once that commit has settled, sent or not, failed or not. */
    onSettle?: () => void;
  } = {},
): EditQueue<P, I, R> {
  let latest: P | null = null;
  let tail: Promise<unknown> = Promise.resolve();

  return {
    publish(projection: P) {
      latest = projection;
    },
    current() {
      return latest;
    },
    push(build: (projection: P) => I | null): Promise<R | null> {
      // Before the first await: a save path running later in the same event -- the
      // click on Back, after the blur that started this -- has to see it already up.
      hooks.onStart?.();
      const run = tail.then(async (): Promise<R | null> => {
        if (latest === null) return null;
        const intent = build(latest);
        if (intent === null) return null;
        const out = await send(intent);
        // Published here, in the same turn as the answer, so the next entry in the
        // queue builds from it. A caller's continuation is a microtask too late.
        latest = out.projection;
        return out;
      });
      // The caller answers its own failure; this only stops one unexpected throw from
      // wedging the queue for the rest of the session.
      tail = run.catch(() => {});
      return (async () => {
        try {
          return await run;
        } finally {
          hooks.onSettle?.();
        }
      })();
    },
  };
}
