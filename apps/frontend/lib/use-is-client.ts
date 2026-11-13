"use client";

import { useSyncExternalStore } from "react";

// Three module-level constants, not inline arrows: useSyncExternalStore
// resubscribes whenever `subscribe` changes identity, and re-reads whenever
// the snapshot getters do.
const subscribe = () => () => {};
const onClient = () => true;
const onServer = () => false;

/**
 * False during the server render and the hydration pass, true afterwards.
 *
 * For values that only exist in the browser and only make sense there — the
 * reader's timezone, their clock, "today". The obvious shape (a mounted flag
 * set in an effect) is a synchronous setState inside an effect, which
 * cascades a second render and is what `react-hooks/set-state-in-effect`
 * objects to. This is the API React added for exactly this question, and it
 * hydrates with the server's answer before switching, so markup matches.
 */
export function useIsClient(): boolean {
  return useSyncExternalStore(subscribe, onClient, onServer);
}
