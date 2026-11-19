"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FormatPopover from "@/components/editor/FormatPopover";
import { domToMarks, marksToHtml, type Mark } from "@/lib/rich-marks";
import { lineCommitDecision } from "@/lib/structured-fields";
import { cn } from "@/lib/utils";

/** Commit a line the person has stopped typing into, without waiting for a blur. */
const IDLE_COMMIT_MS = 2000;

type Props = {
  /** The unit's plain text, as the server has it. */
  text: string;
  /** Only the marks `marksToHtml` can draw (see `unitView`). */
  marks: Mark[];
  /** Raw source runs inside `text`, drawn as uneditable chips. */
  opaque: string[];
  readOnly?: boolean;
  ariaLabel: string;
  className?: string;
  /**
   * Hand this text and its marks to the panel as a `set_line`. It queues them -- one
   * commit at a time, rebuilt against the freshest projection -- so this surface never
   * has to know whether an earlier one is still in flight.
   */
  onCommit: (next: { text: string; marks: Mark[] }) => void;
  /** The surface was emptied, which `set_line` refuses; the line has been put back. */
  onEmptied?: () => void;
  /**
   * Changes whenever the panel's unit set does. A surface still showing text whose commit
   * was refused would otherwise keep it against an id that may now name another line, so
   * it is repainted from the server's text -- unless the caret is in it or a commit is
   * owed, when that text is the person's and is left alone -- but never sent, and the
   * next commit repaints and calls `onStale`.
   */
  epoch?: string;
  /** Text typed under an earlier `epoch` was dropped instead of sent. */
  onStale?: () => void;
};

/**
 * One line unit as a contenteditable: the whole line is one editing surface, not a row
 * of inputs, so bold and links keep working across it (and nest, as the source does).
 *
 * React does not own the inside of this element. The content is written imperatively
 * from `marksToHtml` and read back with `domToMarks`, because re-rendering a
 * contenteditable from props moves the caret to the start of the line on every
 * keystroke. The element is therefore childless as far as React is concerned, and the
 * only thing that writes to it is `write` below.
 */
export default function RichLine({
  text,
  marks,
  opaque,
  readOnly,
  ariaLabel,
  className,
  onCommit,
  onEmptied,
  onStale,
  epoch,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  /** The same element as state, so the format popover re-renders when it appears. */
  const [hostEl, setHostEl] = useState<HTMLDivElement | null>(null);
  const attachHost = useCallback((el: HTMLDivElement | null) => {
    hostRef.current = el;
    setHostEl(el);
  }, []);
  const html = useMemo(() => marksToHtml(text, marks, opaque), [text, marks, opaque]);
  /** The html the surface currently shows, so an unchanged projection is not rewritten. */
  const shownRef = useRef<string | null>(null);
  /** A projection that arrived while the caret was in here, applied when it leaves. */
  const heldRef = useRef<string | null>(null);
  const idleRef = useRef<number | null>(null);
  /** The latest `commit`, for the idle timer and the unmount commit. Assigned below. */
  const commitRef = useRef<(host: HTMLElement) => boolean>(() => false);

  /**
   * What the surface itself held the last time this component painted it or handed text
   * over -- read back through `domToMarks`, not taken from the projection.
   *
   * `marksToHtml` -> `innerHTML` -> `domToMarks` is not the identity. The HTML parser
   * flattens a `<a>` inside a `<a>` into siblings, `domToMarks` drops a `strong` nested
   * inside a `strong` (right for `**x**`, wrong for `*[a *b*]*`), and a U+00A0 the
   * person's own source carried comes back as a space. Judging a commit against the
   * *projection's* marks therefore made a click into a line and out of it look like an
   * edit, and the blur wrote a rewritten line back. Judged against this, a blur with no
   * typing sends nothing -- in those three shapes and in ones nobody has thought of.
   *
   * Refreshed on a handover too, not only on a paint: after a commit the server has the
   * typed text while the surface is still focused and cannot be repainted, so a baseline
   * left at the pre-edit paint would call a revert back to it "unchanged" and drop it.
   */
  const committedRef = useRef<{ text: string; marks: Mark[] } | null>(null);

  const write = useCallback((host: HTMLElement, next: string) => {
    heldRef.current = null;
    shownRef.current = next;
    host.innerHTML = next;
    committedRef.current = domToMarks(host);
  }, []);

  /**
   * Whether the surface may hold text no commit has read yet.
   *
   * An armed timer is exactly that: every keystroke arms one, and a commit reads the
   * DOM as it hands the text over, so once it has there is no debt left here.
   */
  const owes = useCallback(() => idleRef.current !== null, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || shownRef.current === html) return;
    if (document.activeElement === host || owes()) {
      // Never paint over text that has not been committed. Rewriting under the caret
      // also sends it back to the start of the line, and the idle commit below lands
      // while the person is still typing. Held until the surface is settled and empty
      // of debt, where what it shows is what the server has.
      heldRef.current = html;
      return;
    }
    write(host, html);
  }, [html, owes, write]);

  const epochRef = useRef(epoch);
  /** Text typed under an `epoch` that has since changed: never sent, see `commit`. */
  const staleRef = useRef(false);
  useEffect(() => {
    if (epochRef.current === epoch) return;
    epochRef.current = epoch;
    const host = hostRef.current;
    if (!host) return;
    if (owes()) {
      // A commit is owed, so this is typed text: the person's, and not yanked away. It
      // was typed against ids that have since moved, so it is not sent either; `commit`
      // drops it and says why. With nothing typed there is no draft to lose, so a focused
      // row is simply repainted and never told it "changed while you were typing".
      // (`expect` cannot be the net -- the queue builds it from the server's own answer.)
      staleRef.current = true;
      return;
    }
    staleRef.current = false;
    write(host, html);
  }, [epoch, html, owes, write]);

  const clearIdle = useCallback(() => {
    if (idleRef.current === null) return;
    window.clearTimeout(idleRef.current);
    idleRef.current = null;
  }, []);

  /**
   * Commit once the person stops typing. Reads `commitRef` so it sees fresh props, and
   * takes `host` rather than reading `hostRef`, which React nulls on unmount -- the
   * detached element still carries what was typed into it.
   */
  const armIdle = useCallback(
    (host: HTMLElement) => {
      clearIdle();
      idleRef.current = window.setTimeout(() => {
        idleRef.current = null;
        commitRef.current(host);
      }, IDLE_COMMIT_MS);
    },
    [clearIdle],
  );

  /** Hand over what the surface holds, if it is worth sending. True when it did. */
  const commit = useCallback(
    (host: HTMLElement): boolean => {
      clearIdle();
      if (readOnly) return false;
      if (staleRef.current) {
        staleRef.current = false;
        write(host, html);
        onStale?.();
        return false;
      }
      const next = domToMarks(host);
      // A cheap early-out only. The baseline is one answer behind while a commit is in
      // flight, so this can say "send" for text that is already on the server; the panel
      // rebuilds every intent against the freshest projection and drops it if there is
      // nothing left to do.
      const decision = lineCommitDecision(next, committedRef.current ?? { text, marks });
      if (!decision.send) {
        if (decision.reason === "empty") {
          // `set_line` refuses to empty a line: an unprojected line would shift every
          // later unit id. Put it back and let the panel say so -- deleting the whole
          // line is a different intent.
          write(host, html);
          onEmptied?.();
        }
        return false;
      }
      committedRef.current = next;
      onCommit(next);
      return true;
    },
    [clearIdle, html, marks, onCommit, onEmptied, onStale, readOnly, text, write],
  );

  // Kept in a ref so the unmount effect below can stay `[]` -- it must run its cleanup
  // on unmount only, never on a re-render -- and so the retry above reaches the
  // closure that has the answer the server just gave us, not the one it replaced.
  useEffect(() => {
    commitRef.current = commit;
  }, [commit]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    return () => {
      clearIdle();
      // Switching tabs unmounts this panel. A click on another tab blurs the surface
      // first, but a keyboard or programmatic switch need not, and the idle timer dies
      // with the component -- so the last commit happens here, and it really is sent:
      // the panel's queue outlives this component, and a commit that turns out to be
      // redundant is dropped when its turn comes rather than being refused. `host` is
      // captured rather than read from the ref, which React has already detached by
      // now; the detached element still carries what was typed into it.
      commitRef.current(host);
    };
  }, [clearIdle]);

  return (
    <>
      <div
        ref={attachHost}
        role="textbox"
        aria-multiline="false"
        aria-label={ariaLabel}
        aria-readonly={readOnly ? true : undefined}
        contentEditable={!readOnly}
        spellCheck={!readOnly}
        // `rr-input` is the repo's field skin (border, padding, background, focus ring)
        // and sets no font-size, so `text-base md:text-sm` still decides that -- iOS
        // zooms into anything under 16px and never zooms back out. `rich-line` carries
        // what the inline markup looks like, in `globals.css`, because the unlayered
        // `a { color: inherit }` there beats a Tailwind utility.
        className={cn(
          "rich-line whitespace-pre-wrap break-words text-base leading-6 md:text-sm",
          readOnly
            ? "rounded-md border border-dashed border-rule bg-muted/30 px-3 py-2"
            : "rr-input",
          className,
        )}
        onInput={(event) => armIdle(event.currentTarget)}
        onBlur={(event) => {
          const host = hostRef.current;
          if (!host) return;
          // Focus moving into the format popover's url box is not the person leaving the
          // line: committing or repainting here would replace the nodes its saved
          // selection points into.
          const to = event.relatedTarget;
          if (to instanceof Element && to.closest("[data-format-popover]")) return;
          // Nothing sent and nothing owed means nothing will come back to repaint this,
          // so apply whatever arrived while the caret was in here. With a commit owed,
          // painting now would overwrite text that no commit has read yet.
          if (commit(host) || owes()) return;
          if (heldRef.current !== null) write(host, heldRef.current);
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          // A unit is one line; a newline here would be a second line the projection has
          // no unit for.
          event.preventDefault();
          const host = hostRef.current;
          if (host) commit(host);
        }}
      />
      <FormatPopover
        host={readOnly ? null : hostEl}
        onChanged={() => {
          const host = hostRef.current;
          if (host) commitRef.current(host);
        }}
      />
    </>
  );
}
