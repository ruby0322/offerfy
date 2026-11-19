/**
 * Server marks <-> contenteditable HTML.
 *
 * Offsets are Python code points, not UTF-16 units: one astral character (an emoji) is
 * two units in a JS string, so `.length` / `.slice` would be off by one per emoji and
 * mark the wrong words. Everything here indexes `Array.from(text)`.
 *
 * Opaque runs (math, raw text, calls the projection does not understand) are part of
 * `text` as their raw source, in order; `opaque` lists them so the editor can show each
 * as an uneditable chip carrying its source in `data-raw`.
 */

/**
 * All four kinds the projection emits -- `typst_projection._MARK_KINDS`, which
 * `schemas_editing.MarkOut` carries as a bare string. `group` is a `[…]` content group;
 * the brackets are not part of the text, they are this mark.
 */
export type Mark = {
  kind: "strong" | "emph" | "link" | "group";
  start: number;
  end: number;
  url?: string;
};

/** The neutral wrapper a `group` is drawn as: structure, with no styling of its own. */
const GROUP_ATTR = 'data-r="gr"';

/** The part of a DOM node `domToMarks` reads. A live `HTMLElement` is assignable. */
export type MarkNode = {
  nodeType: number;
  nodeName: string;
  textContent: string | null;
  childNodes: ArrayLike<MarkNode>;
  getAttribute?: (name: string) => string | null;
};

const ELEMENT_NODE = 1;
const TEXT_NODE = 3;

function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s: string): string {
  return escapeText(s).replace(/"/g, "&quot;");
}

/** Code-point ranges of each opaque run, found left to right in `text` in order. */
function chipRanges(cps: string[], opaque: string[]): Map<number, number> {
  const chips = new Map<number, number>(); // start -> end
  let from = 0;
  for (const raw of opaque) {
    const want = Array.from(raw);
    if (want.length === 0) continue;
    for (let i = from; i + want.length <= cps.length; i++) {
      if (want.every((c, k) => cps[i + k] === c)) {
        chips.set(i, i + want.length);
        from = i + want.length;
        break;
      }
    }
  }
  return chips;
}

export function marksToHtml(text: string, marks: Mark[], opaque: string[]): string {
  const cps = Array.from(text);
  const chips = chipRanges(cps, opaque);
  // Outermost first; the sort is stable, so equal ranges keep the server's pre-order.
  const sorted = marks
    .map((m) => ({ ...m, start: Math.max(0, m.start), end: Math.min(cps.length, m.end) }))
    .filter((m) => m.end > m.start)
    .sort((a, b) => a.start - b.start || b.end - a.end);

  function render(lo: number, hi: number, queue: Mark[]): string {
    let out = "";
    let i = lo;
    while (i < hi) {
      const next = queue[0];
      if (next && next.start <= i) {
        queue.shift();
        // A mark overhanging its parent is clamped to it (B 3-8 under A 0-5 becomes
        // 3-5, losing its tail). The server cannot emit this: marks come from
        // well-formed Typst, where they nest. "overlapping marks are truncated" pins it.
        const end = Math.min(next.end, hi);
        const inner: Mark[] = [];
        while (queue.length && queue[0].start < end) inner.push(queue.shift()!);
        const body = render(i, end, inner);
        if (next.kind === "strong") out += `<b>${body}</b>`;
        else if (next.kind === "emph") out += `<i>${body}</i>`;
        // Each kind is named: a `<a data-url="">` fallback would turn a group into a
        // link with no url, which `serialize_unit` refuses -- the line would look
        // editable and never save. An unknown fifth kind is better as plain text.
        else if (next.kind === "group") out += `<span ${GROUP_ATTR}>${body}</span>`;
        else if (next.kind === "link") {
          out += `<a data-url="${escapeAttr(next.url ?? "")}">${body}</a>`;
        } else out += body;
        i = end;
        continue;
      }
      const stop = Math.min(hi, next ? next.start : hi);
      const chipEnd = chips.get(i);
      // The server cannot emit a mark boundary inside a chip: marks are well-formed
      // Typst and an opaque run is atomic. If one ever arrives, `chipEnd > stop` and the
      // run falls through to the plain-text path below: nothing is lost, but the chip
      // becomes editable text. "a mark boundary inside a chip" pins that.
      if (chipEnd !== undefined && chipEnd <= stop) {
        const raw = escapeText(cps.slice(i, chipEnd).join(""));
        out += `<span data-r="op" contenteditable="false" data-raw="${escapeAttr(
          cps.slice(i, chipEnd).join(""),
        )}">${raw}</span>`;
        i = chipEnd;
        continue;
      }
      // plain text up to the next mark or chip start
      let j = i + 1;
      while (j < stop && !chips.has(j)) j++;
      out += escapeText(cps.slice(i, j).join(""));
      i = j;
    }
    return out;
  }

  return render(0, cps.length, sorted);
}

/**
 * Elements that end a line of their own accord. A paste of two lines arrives as either
 * a `<br>` or a run of these, and neither contributes any text of its own -- so without
 * them two pasted lines committed as one word: "line onelineand two".
 */
const BLOCK = new Set([
  "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
  "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
  "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "td", "th",
  "tr", "ul",
]);

export function domToMarks(root: MarkNode): { text: string; marks: Mark[] } {
  let text = "";
  let count = 0; // code points so far
  const marks: Mark[] = [];
  const parents = new Map<Mark, Mark | null>(); // the enclosing mark, if any
  /**
   * A line break seen but not yet written. A unit is one line, so a break becomes a
   * single space -- but only once something follows it, because contenteditable leaves
   * a `<br>` at the end of a line it has finished with and a trailing space would then
   * be committed as part of the person's text.
   */
  let pending = false;

  /** Write the pending break, unless `next` begins with its own whitespace. */
  function settle(next?: string): void {
    if (!pending) return;
    pending = false;
    const alreadySpaced = next !== undefined && /^\s/.test(next);
    if (text === "" || /\s$/.test(text) || alreadySpaced) return;
    text += " ";
    count += 1;
  }

  function append(s: string): void {
    if (s === "") return;
    settle(s);
    text += s;
    count += Array.from(s).length;
  }

  function breakHere(): void {
    if (text !== "") pending = true;
  }

  function walk(node: MarkNode, active: Set<string>, parent: Mark | null): void {
    if (node.nodeType === TEXT_NODE) {
      // contenteditable turns a trailing or doubled space into U+00A0; never let that
      // reach the person's Typst source.
      append((node.textContent ?? "").replace(/ /g, " "));
      return;
    }
    if (node.nodeType !== ELEMENT_NODE) return;

    const raw = node.getAttribute?.("data-raw") ?? null;
    if (raw !== null) {
      append(raw);
      return;
    }

    const name = node.nodeName.toLowerCase();
    if (name === "br") {
      breakHere();
      return;
    }
    if (BLOCK.has(name)) breakHere();
    // Only an `<a>` this module drew is a link. A pasted hyperlink arrives from the
    // clipboard as `<a href="...">`, and trusting that would write a url into the
    // person's Typst source that no part of the UI ever showed them -- including a
    // `javascript:` one. `marksToHtml` emits `data-url` and nothing else, so there is
    // no legitimate producer of `href` left to read.
    const url = name === "a" ? (node.getAttribute?.("data-url") ?? null) : null;
    let kind: Mark["kind"] | null = null;
    if (name === "b" || name === "strong") kind = "strong";
    else if (name === "i" || name === "em") kind = "emph";
    else if (name === "a") kind = url === null ? null : "link";
    else if (node.getAttribute?.("data-r") === "gr") kind = "group";

    // Unknown elements (a browser's <div>, <font>, <br>) contribute their text, no mark.
    // A nested mark of a kind already open adds nothing for the two that are pure
    // styling: `**x**` is `*x*`. Not for a group -- `[[x]]` is two bracket pairs and
    // dropping one rewrites the line -- and not for a link, which can nest.
    if (kind === null || ((kind === "strong" || kind === "emph") && active.has(kind))) {
      for (let i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i], active, parent);
      return;
    }

    // A pending break belongs before this mark, not inside it, or every offset in it
    // would be one short of where the text puts it.
    settle();
    const mark: Mark = { kind, start: count, end: count };
    if (kind === "link") mark.url = url ?? "";
    parents.set(mark, parent);
    marks.push(mark); // pre-order: the outer mark precedes the ones inside it
    const inner = new Set(active).add(kind);
    for (let i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i], inner, mark);
    mark.end = count;
    if (mark.end === mark.start) marks.splice(marks.indexOf(mark), 1);
  }

  for (let i = 0; i < root.childNodes.length; i++) walk(root.childNodes[i], new Set(), null);
  return { text, marks: mergeTouching(marks, parents) };
}

/**
 * `<b>a</b><b>b</b>` is one bold run: merge same-kind marks that touch and share a url.
 * Only siblings merge (same enclosing mark). Across nesting levels a merge would make a
 * child overhang its parent, and the clamp in `marksToHtml` would then drop its tail.
 *
 * Groups never merge: `[a][b]` and `[ab]` are different source, and a group is there to
 * bound something rather than to style it.
 */
function mergeTouching(marks: Mark[], parents: Map<Mark, Mark | null>): Mark[] {
  const out: Mark[] = [];
  const merged = new Map<Mark, Mark>(); // dropped mark -> the mark that absorbed it
  const home = (m: Mark | null | undefined) => (m ? (merged.get(m) ?? m) : null);
  for (const m of marks) {
    const parent = home(parents.get(m));
    const prev =
      m.kind === "group"
        ? undefined
        : out.find(
            (p) =>
              p.kind === m.kind &&
              p.end === m.start &&
              p.url === m.url &&
              home(parents.get(p)) === parent,
          );
    if (prev) {
      prev.end = m.end;
      merged.set(m, prev);
    } else {
      out.push(m);
    }
  }
  return out;
}
