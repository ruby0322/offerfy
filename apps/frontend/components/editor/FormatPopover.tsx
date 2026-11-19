"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { buttonsFor, segmentsOf, type Segment } from "@/lib/format-selection";
import { normalizeLinkUrl } from "@/lib/link-url";
import { placePopover, type Placement, type Rect } from "@/lib/popover-placement";

type Props = {
  /** The `RichLine` surface this popover serves; null while it is read-only or unmounted. */
  host: HTMLElement | null;
  /** The DOM now holds the new marks: commit the line. */
  onChanged: () => void;
};

type Snap = {
  /**
   * A clone, so it is the person's selection as it was when the surface last had one.
   * Focusing the URL input drops the live selection; this is what gets put back.
   */
  range: Range;
  segments: Segment<Node>[];
  /** The selection covers some of an opaque chip, so bold and italic are refused. */
  touchesChip: boolean;
  /** Every link the selection touches. */
  links: HTMLAnchorElement[];
  /** The one link the whole selection (or the caret) sits inside, if there is one. */
  editing: HTMLAnchorElement | null;
  bold: boolean;
  italic: boolean;
};

const LINK = "a[data-url]";

function elementOf(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === 1 ? (node as Element) : node.parentElement;
}

function linkOf(host: HTMLElement, node: Node | null): HTMLAnchorElement | null {
  const a = elementOf(node)?.closest<HTMLAnchorElement>(LINK) ?? null;
  return a && host.contains(a) ? a : null;
}

function unwrap(a: HTMLElement): void {
  const parent = a.parentNode;
  if (!parent) return;
  while (a.firstChild) parent.insertBefore(a.firstChild, a);
  parent.removeChild(a);
}

/** Whether the nodes a saved range points into are still the surface's own. */
function isLive(host: HTMLElement, snap: Snap): boolean {
  const { range } = snap;
  return (
    host.contains(range.startContainer) &&
    host.contains(range.endContainer) &&
    snap.segments.every((s) => host.contains(s.node)) &&
    (!snap.editing || host.contains(snap.editing)) &&
    snap.links.every((a) => host.contains(a))
  );
}

function read(host: HTMLElement): Snap | null {
  const sel = document.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const live = sel.getRangeAt(0);
  if (!host.contains(live.startContainer) || !host.contains(live.endContainer)) return null;
  const range = live.cloneRange();
  const { segments, touchesChip } = range.collapsed
    ? { segments: [], touchesChip: false }
    : segmentsOf<Node>(host, range);
  const startLink = linkOf(host, range.startContainer);
  const editing = startLink && startLink === linkOf(host, range.endContainer) ? startLink : null;
  // A bare caret outside a link, or a selection of nothing but chips, has nothing to format.
  if (segments.length === 0 && !editing) return null;
  const links = new Set<HTMLAnchorElement>();
  if (editing) links.add(editing);
  for (const s of segments) {
    const a = linkOf(host, s.node);
    if (a) links.add(a);
  }
  const state = (cmd: string) => {
    try {
      return document.queryCommandState(cmd);
    } catch {
      return false;
    }
  };
  return {
    range,
    segments,
    touchesChip,
    links: [...links],
    editing,
    bold: state("bold"),
    italic: state("italic"),
  };
}

function restore(host: HTMLElement, snap: Snap): void {
  host.focus({ preventScroll: true });
  const sel = document.getSelection();
  if (!sel) return;
  sel.removeAllRanges();
  sel.addRange(snap.range.cloneRange());
}

function anchorRect(host: HTMLElement, snap: Snap): Rect {
  const r =
    snap.range.collapsed && snap.editing
      ? snap.editing.getBoundingClientRect()
      : snap.range.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return host.getBoundingClientRect();
  return r;
}

function scrollContainerRect(host: HTMLElement): Rect {
  for (let p = host.parentElement; p; p = p.parentElement) {
    if (/(auto|scroll|overlay)/.test(getComputedStyle(p).overflowY)) {
      return p.getBoundingClientRect();
    }
  }
  return { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
}

export default function FormatPopover({ host, onChanged }: Props) {
  const t = useTranslations("editor");
  const popRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [snap, setSnapState] = useState<Snap | null>(null);
  const snapRef = useRef<Snap | null>(null);
  const [rowOpen, setRowOpen] = useState(false);
  /** Read by the `selectionchange` listener: while the URL row is open, selection is the input's. */
  const rowOpenRef = useRef(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<"scheme" | "invalid" | "lost" | "chip" | null>(null);
  const [pos, setPos] = useState<Placement | null>(null);

  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

  const setSnap = useCallback((next: Snap | null) => {
    snapRef.current = next;
    setSnapState(next);
  }, []);

  const refresh = useCallback(() => {
    if (!host || rowOpenRef.current) return;
    setSnap(read(host));
  }, [host, setSnap]);

  // Follow the selection. `selectionchange` fires on the document, not the surface.
  useEffect(() => {
    if (!host) return;
    const onSelection = () => {
      if (rowOpenRef.current) return;
      const next = read(host);
      if (next === null && snapRef.current === null) return;
      setSnap(next);
      setError(null);
    };
    document.addEventListener("selectionchange", onSelection);
    return () => {
      document.removeEventListener("selectionchange", onSelection);
      rowOpenRef.current = false;
      setRowOpen(false);
      setSnap(null);
    };
  }, [host, setSnap]);

  const openRow = useCallback(
    (from: Snap) => {
      snapRef.current = from;
      setSnapState(from);
      rowOpenRef.current = true;
      setDraft(from.editing?.getAttribute("data-url") ?? "");
      setError(null);
      setRowOpen(true);
    },
    [],
  );

  useEffect(() => {
    if (!rowOpen) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [rowOpen]);

  const closeRow = useCallback(
    (back: boolean) => {
      if (!rowOpenRef.current) return;
      rowOpenRef.current = false;
      setRowOpen(false);
      setError(null);
      const from = snapRef.current;
      if (back && host && from && isLive(host, from)) restore(host, from);
      // Selection changes made while the row was open were ignored on purpose; take the
      // surface's current one now.
      window.setTimeout(refresh, 0);
    },
    [host, refresh],
  );

  const changed = useCallback(() => {
    onChangedRef.current();
  }, []);

  const toggle = useCallback(
    (command: "bold" | "italic", from: Snap) => {
      if (!host || from.range.collapsed) return;
      if (from.touchesChip) {
        setError("chip");
        return;
      }
      if (!isLive(host, from)) {
        setError("lost");
        return;
      }
      restore(host, from);
      // Without this a browser may answer with `<span style="font-weight: bold">`, which
      // `domToMarks` reads as plain text and drops. `<b>`/`<strong>` and `<i>`/`<em>`
      // are all read, so which one a browser picks does not matter.
      document.execCommand("styleWithCSS", false, "false");
      document.execCommand(command);
      changed();
      refresh();
    },
    [changed, host, refresh],
  );

  const removeLinks = useCallback(
    (from: Snap) => {
      if (!host) return;
      if (!isLive(host, from)) {
        setError("lost");
        return;
      }
      restore(host, from);
      for (const a of from.editing ? [from.editing] : from.links) unwrap(a);
      changed();
      refresh();
    },
    [changed, host, refresh],
  );

  const submit = useCallback(() => {
    const from = snapRef.current;
    if (!host || !from) return;
    const result = normalizeLinkUrl(draft);
    if (result.kind === "rejected") {
      setError(result.reason);
      return;
    }
    if (!isLive(host, from)) {
      // The surface was repainted from the server while the person typed the url; the
      // nodes this selection pointed into are gone.
      setError("lost");
      return;
    }
    // Segments are text nodes by construction. Checked before anything is touched, so a
    // change to that never becomes a link that quietly was not made.
    const segments: { node: Text; start: number; end: number }[] = [];
    for (const s of from.segments) {
      if (!(s.node instanceof Text)) {
        setError("lost");
        return;
      }
      segments.push({ node: s.node, start: s.start, end: s.end });
    }
    // The selection goes back first. Everything below works from the cloned range, and
    // the person is left looking at what they selected rather than at a lost caret.
    restore(host, from);
    rowOpenRef.current = false;
    setRowOpen(false);
    setError(null);

    if (result.kind === "remove") {
      // An empty url is not a link `serialize_unit` will take, so "cleared" means gone.
      for (const a of from.editing ? [from.editing] : from.links) unwrap(a);
    } else if (from.editing) {
      from.editing.setAttribute("data-url", result.url);
    } else {
      // Wrap each run of text separately rather than `extractContents()` on the whole
      // range: extracting across a `[...]` group clones the group wrapper into the link,
      // which turns one group into two and rewrites the line's brackets. Touching links
      // with the same url merge back into one mark in `domToMarks`.
      //
      // Each covered text node (one segment per node, which `segmentsOf` guarantees and
      // its tests pin: a second cut in the same node would use stale offsets) is first
      // split into [before | covered | after]. A link the selection only partly overlaps
      // is dissolved (no nested links), and then *every* piece of it that was not
      // selected -- the halves around a cut, other text nodes, chips -- is wrapped again
      // in an `<a>` with the old url, so nothing the person did not select loses a link.
      const covered: Text[] = [];
      for (const seg of segments) {
        const length = seg.node.length;
        covered.push(seg.start > 0 ? seg.node.splitText(seg.start) : seg.node);
        const mid = covered[covered.length - 1];
        if (seg.end < length) mid.splitText(seg.end - seg.start);
      }
      const isCovered = new Set<Node>(covered);
      const keep = new Map<Node, string[]>(); // uncovered leaf -> its links' urls, inner first
      const collect = (node: Node): void => {
        const el = node instanceof Element ? node : null;
        if (el?.hasAttribute("data-raw") || (node instanceof Text && node.length > 0)) {
          if (isCovered.has(node) || keep.has(node)) return;
          const urls: string[] = [];
          for (let a = linkOf(host, node); a; a = linkOf(host, a.parentNode)) {
            urls.push(a.getAttribute("data-url") ?? "");
          }
          keep.set(node, urls);
          return;
        }
        node.childNodes.forEach(collect);
      };
      for (const a of from.links) a.childNodes.forEach(collect);
      for (const a of from.links) unwrap(a);
      const wrap = (node: Node, url: string): HTMLAnchorElement => {
        const a = document.createElement("a");
        a.setAttribute("data-url", url);
        node.parentNode?.insertBefore(a, node);
        a.appendChild(node);
        return a;
      };
      for (const [node, urls] of keep) {
        let inner: Node = node;
        for (const url of urls) inner = wrap(inner, url);
      }
      const made = covered.map((node) => wrap(node, result.url));
      if (made.length > 0) {
        const sel = document.getSelection();
        const after = document.createRange();
        after.setStartBefore(made[0]);
        after.setEndAfter(made[made.length - 1]);
        sel?.removeAllRanges();
        sel?.addRange(after);
      }
    }
    changed();
    window.setTimeout(refresh, 0);
  }, [changed, draft, host, refresh]);

  // ⌘B / ⌘I / ⌘K on the surface itself.
  useEffect(() => {
    if (!host) return;
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key !== "b" && key !== "i" && key !== "k") return;
      event.preventDefault();
      const from = read(host);
      if (!from) return;
      if (key === "k") openRow(from);
      else toggle(key === "b" ? "bold" : "italic", from);
    };
    host.addEventListener("keydown", onKey);
    return () => host.removeEventListener("keydown", onKey);
  }, [host, openRow, toggle]);

  const reposition = useCallback(() => {
    const el = popRef.current;
    const from = snapRef.current;
    if (!host || !el || !from) return;
    setPos(
      placePopover({
        anchor: anchorRect(host, from),
        container: scrollContainerRect(host),
        viewport: { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight },
        size: { width: el.offsetWidth, height: el.offsetHeight },
        prefer: window.matchMedia("(pointer: coarse)").matches ? "below" : "above",
      }),
    );
  }, [host]);

  // Measured after paint-prep, before the browser paints: the size depends on what is
  // showing (buttons, the url row, an error line), and the first frame is hidden.
  useLayoutEffect(() => {
    if (snap || rowOpen) reposition();
  }, [snap, rowOpen, error, reposition]);

  useEffect(() => {
    if (!snap && !rowOpen) return;
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [snap, rowOpen, reposition]);

  if (!host || !snap) return null;

  const mod =
    typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";
  const shown = pos !== null && !pos.hidden;
  const editingUrl = snap.editing?.getAttribute("data-url") ?? "";
  const errorText =
    error === "scheme"
      ? t("formatUrlScheme")
      : error === "invalid"
        ? t("formatUrlInvalid")
        : error === "lost"
          ? t("formatLost")
          : error === "chip"
            ? t("formatChipBlocked")
            : null;
  const plan = buttonsFor({
    collapsed: snap.range.collapsed,
    editing: snap.editing !== null,
    linkCount: snap.links.length,
    touchesChip: snap.touchesChip,
  });
  // A chip in the selection is a standing reason, not an event, so it is derived.
  const notice = plan.format === "chip" ? t("formatChipBlocked") : errorText;
  /**
   * A button acts on what the surface holds now, not on the snapshot the last render
   * drew: the read is free, and a selection that has gone says so instead of acting on
   * stale nodes.
   */
  const withFresh = (run: (s: Snap) => void) => {
    const now = host ? read(host) : null;
    if (!now) {
      setError("lost");
      return;
    }
    run(now);
  };

  return (
    <div
      ref={popRef}
      role="toolbar"
      aria-label={t("formatBar")}
      data-format-popover=""
      data-row={rowOpen ? "true" : "false"}
      className="format-popover"
      style={{
        position: "fixed",
        left: pos && !pos.hidden ? pos.left : 0,
        top: pos && !pos.hidden ? pos.top : 0,
        visibility: shown ? "visible" : "hidden",
        pointerEvents: shown ? "auto" : "none",
      }}
      // Every button cancels mousedown so the surface keeps focus, and with it the
      // selection. The input is the one thing that has to take focus.
      onMouseDown={(event) => {
        if (event.target !== inputRef.current) event.preventDefault();
      }}
    >
      {rowOpen ? (
        <>
          <div className="format-popover-row">
            <input
              ref={inputRef}
              type="text"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={draft}
              aria-label={t("formatUrlLabel")}
              aria-invalid={errorText ? true : undefined}
              placeholder="example.com"
              onChange={(event) => {
                setDraft(event.target.value);
                if (error) setError(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submit();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  closeRow(true);
                }
              }}
              onBlur={(event) => {
                const to = event.relatedTarget;
                if (to instanceof Node && popRef.current?.contains(to)) return;
                closeRow(false);
              }}
            />
            <button type="button" onClick={submit}>
              {t("formatApply")}
            </button>
          </div>
          {errorText ? (
            <p role="alert" className="format-popover-error">
              {errorText}
            </p>
          ) : null}
        </>
      ) : (
        <>
          <div className="format-popover-row">
            {plan.format === "hidden" ? null : (
              <>
                <button
                  type="button"
                  aria-pressed={snap.bold}
                  disabled={plan.format === "chip"}
                  title={`${t("formatBold")} (${mod}B)`}
                  aria-label={t("formatBold")}
                  onClick={() => withFresh((s) => toggle("bold", s))}
                >
                  <b>B</b>
                </button>
                <button
                  type="button"
                  aria-pressed={snap.italic}
                  disabled={plan.format === "chip"}
                  title={`${t("formatItalic")} (${mod}I)`}
                  aria-label={t("formatItalic")}
                  onClick={() => withFresh((s) => toggle("italic", s))}
                >
                  <i>I</i>
                </button>
              </>
            )}
            {plan.link === "edit" ? (
              <>
                {/* Shown as text and nothing more: it is never an `href`, so it cannot be
                    followed from here. */}
                <span className="format-popover-url" title={editingUrl}>
                  {editingUrl}
                </span>
                <button
                  type="button"
                  title={`${t("formatEditUrl")} (${mod}K)`}
                  onClick={() => withFresh(openRow)}
                >
                  {t("formatEditUrl")}
                </button>
              </>
            ) : (
              <button
                type="button"
                title={`${t("formatLink")} (${mod}K)`}
                onClick={() => withFresh(openRow)}
              >
                {t("formatLink")}
              </button>
            )}
            {plan.remove ? (
              <button type="button" onClick={() => withFresh(removeLinks)}>
                {t("formatRemoveLink")}
              </button>
            ) : null}
          </div>
          {notice ? (
            <p role="alert" className="format-popover-error">
              {notice}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
