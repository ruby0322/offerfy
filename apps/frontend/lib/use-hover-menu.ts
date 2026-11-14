"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * How long a menu outlives the cursor leaving it.
 *
 * Long enough to cross the gap between a pill and its panel without a steady
 * hand, short enough that sweeping along the facet row does not leave a
 * trail of open menus behind the pointer.
 */
const GRACE_MS = 240;

type Handlers = {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onMouseDownCapture: () => void;
  onFocusCapture: () => void;
};

export type HoverMenu<T extends HTMLElement> = {
  open: boolean;
  /**
   * Goes on the element wrapping BOTH the button and the panel.
   *
   * A callback ref rather than the ref object itself: the element is the
   * hook's own business (it needs it to tell an outside click from an inside
   * one), and a ref handed back to a component is a ref that component is
   * then not allowed to read during render — react-hooks/refs is right about
   * that, and this keeps the question from arising.
   */
  box: (node: T | null) => void;
  /** Spread onto that same wrapper. */
  hover: Handlers;
  /** For the button: opens pinned, or closes. */
  toggle: () => void;
  /** For anything inside that ends the interaction, e.g. choosing an option. */
  close: () => void;
};

/**
 * A menu that opens on hover and can actually be used once it is open.
 *
 * Hover alone is not enough to drive a panel that hangs below its button:
 * the panel is positioned clear of the button, so the few pixels in between
 * belong to neither, and closing the moment the cursor leaves the button
 * closes the menu under the pointer on its way in. Worse, a menu with a
 * field in it (the employer list has one) cannot be typed into at all if
 * the cursor has to stay parked on top of it.
 *
 * So leaving starts a timer instead of closing, and two things cancel it:
 *
 *  - re-entering the wrapper, which includes the panel, since the panel is a
 *    DOM child of it even though it is positioned outside its box; and
 *  - *engaging* the menu — a click or a focus anywhere inside it pins it
 *    open, after which only Escape, a click outside, or choosing something
 *    closes it, and the cursor is free to go anywhere.
 *
 * The pin is what makes the distinction feel right rather than arbitrary:
 * a menu you merely hovered past closes itself, a menu you reached into
 * stays until you are done with it.
 */
export function useHoverMenu<T extends HTMLElement>(): HoverMenu<T> {
  const [open, setOpen] = useState(false);
  const node = useRef<T | null>(null);
  const box = useCallback((el: T | null) => {
    node.current = el;
  }, []);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A ref, not state: nothing renders differently because of it, and the
  // handlers that read it must see the write immediately.
  const pinned = useRef(false);

  const stopTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    stopTimer();
    pinned.current = false;
    setOpen(false);
  }, [stopTimer]);

  const pin = useCallback(() => {
    stopTimer();
    pinned.current = true;
  }, [stopTimer]);

  const enter = useCallback(() => {
    stopTimer();
    setOpen(true);
  }, [stopTimer]);

  const leave = useCallback(() => {
    if (pinned.current) return;
    stopTimer();
    timer.current = setTimeout(() => {
      timer.current = null;
      setOpen(false);
    }, GRACE_MS);
  }, [stopTimer]);

  const toggle = useCallback(() => {
    if (open) {
      close();
      return;
    }
    pin();
    setOpen(true);
  }, [open, close, pin]);

  // A pending close must not fire into an unmounted component: the facet row
  // is rebuilt on every navigation, and every filter click is one.
  useEffect(() => stopTimer, [stopTimer]);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!node.current?.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return {
    open,
    box,
    toggle,
    close,
    hover: {
      onMouseEnter: enter,
      onMouseLeave: leave,
      onMouseDownCapture: pin,
      onFocusCapture: pin,
    },
  };
}
