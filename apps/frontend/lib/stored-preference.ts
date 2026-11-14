import { useCallback, useSyncExternalStore } from "react";

/**
 * A small per-browser preference (which view, which tab) in localStorage.
 *
 * `useSyncExternalStore` rather than read-it-in-an-effect, which is the
 * obvious version and wrong twice: it sets state in the effect body, and it
 * renders the default first and the stored value a frame later, so the
 * control visibly flips after hydration. This hook hands React a server
 * snapshot and a client snapshot, which is the case it exists for.
 *
 * Storage throws in a private window and comes back empty when site data is
 * cleared, so every access is wrapped and the default stands.
 */

const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  // Another tab changing the same key should not leave this one stale.
  const onStorage = () => onChange();
  try {
    window.addEventListener("storage", onStorage);
  } catch {
    /* no window; nothing to subscribe to */
  }
  return () => {
    listeners.delete(onChange);
    try {
      window.removeEventListener("storage", onStorage);
    } catch {
      /* ignore */
    }
  };
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function useStoredPreference<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (next: T) => void] {
  const snapshot = useCallback(() => {
    const raw = read(key);
    return raw !== null && (allowed as readonly string[]).includes(raw)
      ? (raw as T)
      : fallback;
  }, [key, allowed, fallback]);

  // The server has no storage, so it always renders the fallback; React
  // reconciles the stored value on hydration without a mismatch warning.
  const value = useSyncExternalStore(subscribe, snapshot, () => fallback);

  const set = useCallback(
    (next: T) => {
      try {
        window.localStorage.setItem(key, next);
      } catch {
        /* ignore; the choice just does not persist */
      }
      for (const listener of listeners) listener();
    },
    [key],
  );

  return [value, set];
}
