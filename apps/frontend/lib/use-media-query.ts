"use client";

import { useMemo, useSyncExternalStore } from "react";

// The width below which the editor stops being two columns. Panes narrower than
// this cannot hold a page of Typst and a page of preview at once.
export const NARROW_SCREEN = "(max-width: 899px)";

/**
 * Tracks a media query. Returns false on the server and for the first paint, so
 * a component that branches on it should not be the first thing a reader sees.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useMemo(
    () => (onStoreChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onStoreChange);
      return () => media.removeEventListener("change", onStoreChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/**
 * The height of the part of the window the reader can actually see, which on a
 * phone is what is left above the on-screen keyboard. Returns null where there
 * is nothing to correct for (no visualViewport, or a layout that does not need
 * it), so callers can fall back to dvh.
 */
export function useVisualViewportHeight(enabled: boolean): number | null {
  const subscribe = useMemo(
    () => (onStoreChange: () => void) => {
      const viewport = enabled ? window.visualViewport : null;
      if (!viewport) return () => {};
      viewport.addEventListener("resize", onStoreChange);
      viewport.addEventListener("scroll", onStoreChange);
      return () => {
        viewport.removeEventListener("resize", onStoreChange);
        viewport.removeEventListener("scroll", onStoreChange);
      };
    },
    [enabled],
  );
  return useSyncExternalStore(
    subscribe,
    () => {
      if (!enabled) return null;
      const viewport = window.visualViewport;
      if (!viewport) return null;
      // Rounded so a sub-pixel scroll offset does not re-render on every frame.
      return Math.round(viewport.height);
    },
    () => null,
  );
}
