"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

/** `[open, setOpen]` — the one piece of state both cards share. */
type Tie = [boolean, (open: boolean) => void];

const TieContext = createContext<Tie | null>(null);

/**
 * The live board's two cards open and close as one.
 *
 * They are a pair, not an accordion: "what just landed" and "who is hiring
 * hardest" are two halves of the same glance, and a row with one card open
 * beside one still shut reads as a card that was opened and found empty —
 * a grid item stretches to its row, so the shut one grew to match without
 * gaining anything to show. Tying them is the fix that keeps both halves of
 * the glance, rather than making the pair look broken half the time.
 *
 * Still `<details>` underneath, so each summary is a real disclosure button
 * to a screen reader and the keyboard, and so the cards render closed and
 * stay usable before this file has hydrated — the sync is the only part
 * that needs JavaScript.
 */
export function PulseCards({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <TieContext.Provider value={[open, setOpen]}>{children}</TieContext.Provider>;
}

type CardProps = {
  /** The summary's content: heading, employer marks, caret. */
  head: ReactNode;
  /** The rows, and the foot link under them. */
  children: ReactNode;
};

/**
 * One card. `open` is the shared state, and `toggle` writes back to it, so a
 * click on either summary moves both: the browser flips the card the user
 * actually clicked, the event tells us, and the re-render carries the other
 * one along. Setting `open` on a card that is already in that state is what
 * stops this chasing its own tail — the second card's `toggle` fires, agrees,
 * and nothing further happens.
 */
export function PulseCard({ head, children }: CardProps) {
  const tie = useContext(TieContext);
  if (!tie) throw new Error("<PulseCard> belongs inside <PulseCards>");
  const [open, setOpen] = tie;

  return (
    <details
      className="jobs-pulse-card"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="jobs-pulse-head">{head}</summary>
      {children}
    </details>
  );
}
