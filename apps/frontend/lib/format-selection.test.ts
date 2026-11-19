import assert from "node:assert/strict";
import test from "node:test";

import { buttonsFor, segmentsOf, type SelNode, type SelRange } from "./format-selection";

// Type-level proofs, checked by `tsc --noEmit`: a live Node and Range satisfy the
// structural shapes with no cast. If either stops being assignable, these stop compiling.
const _nodeIsSelNode: Node extends SelNode<Node> ? true : never = true;
const _rangeIsSelRange: Range extends SelRange<Node> ? true : never = true;
void _nodeIsSelNode;
void _rangeIsSelRange;

type T = {
  name: string;
  nodeType: number;
  textContent: string | null;
  childNodes: T[];
  getAttribute?: (name: string) => string | null;
};

const text = (value: string): T => ({
  name: "#text",
  nodeType: 3,
  textContent: value,
  childNodes: [],
});

function el(name: string, attrs: Record<string, string>, kids: T[]): T {
  return {
    name,
    nodeType: 1,
    textContent: kids.map((k) => k.textContent ?? "").join(""),
    childNodes: kids,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
  };
}

const chip = (raw: string) => el("span", { "data-raw": raw }, [text(raw)]);

/** What the segments say they cover, joined. */
function covered(tree: T, range: SelRange<T>): string {
  return segmentsOf(tree, range)
    .segments.map((s) => (s.node.textContent ?? "").slice(s.start, s.end))
    .join("|");
}

test("mid-node to mid-another covers the tails and the middle, whole", () => {
  const a = text("hello "), inner = text("big"), c = text(" world");
  const root = el("div", {}, [a, el("b", {}, [inner]), c]);
  assert.equal(
    covered(root, { startContainer: a, startOffset: 2, endContainer: c, endOffset: 3 }),
    "llo |big| wo",
  );
});

test("an end on a <b> boundary does not cover the bold text it only touches", () => {
  const a = text("ab"), inner = text("cd");
  const b = el("b", {}, [inner]);
  const root = el("div", {}, [a, b]);
  const sel = (endContainer: T, endOffset: number) => ({
    startContainer: a, startOffset: 1, endContainer, endOffset,
  });
  assert.equal(covered(root, sel(root, 1)), "b"); // before the <b>
  assert.equal(covered(root, sel(b, 0)), "b"); // inside it, before its first child
  assert.equal(covered(root, sel(inner, 0)), "b"); // at the start of its text
  assert.equal(covered(root, sel(inner, 1)), "b|c"); // one character in
});

test("a start at the end of a text node does not cover that node", () => {
  const a = text("ab"), b = text("cd");
  const root = el("div", {}, [a, b]);
  assert.equal(
    covered(root, { startContainer: a, startOffset: 2, endContainer: b, endOffset: 1 }),
    "c",
  );
});

test("a selection containing a chip covers the text around it and says it met a chip", () => {
  const a = text("a"), c = chip("$x$"), z = text("z");
  const root = el("div", {}, [a, c, z]);
  const r = segmentsOf(root, { startContainer: a, startOffset: 0, endContainer: z, endOffset: 1 });
  assert.deepEqual(r.segments.map((s) => s.node.textContent), ["a", "z"]);
  assert.equal(r.touchesChip, true);
});

test("a selection that ends inside a chip's text still reports the chip, and never segments it", () => {
  const a = text("a"), raw = text("$xy$");
  const c = el("span", { "data-raw": "$xy$" }, [raw]);
  const root = el("div", {}, [a, c]);
  const r = segmentsOf(root, { startContainer: a, startOffset: 0, endContainer: raw, endOffset: 2 });
  assert.deepEqual(r.segments.map((s) => s.node.textContent), ["a"]);
  assert.equal(r.touchesChip, true);
});

test("a selection that only borders a chip does not report one", () => {
  const a = text("ab"), c = chip("$x$");
  const root = el("div", {}, [a, c]);
  const r = segmentsOf(root, { startContainer: a, startOffset: 0, endContainer: root, endOffset: 1 });
  assert.equal(r.touchesChip, false);
  assert.equal(r.segments.length, 1);
});

test("a group wrapper is transparent: its text is covered like any other", () => {
  const x = text("x"), yy = text("yy"), z = text("z");
  const root = el("div", {}, [x, el("span", { "data-r": "gr" }, [yy]), z]);
  const r = segmentsOf(root, { startContainer: x, startOffset: 0, endContainer: z, endOffset: 1 });
  assert.deepEqual(r.segments.map((s) => [s.node.textContent, s.start, s.end]), [
    ["x", 0, 1],
    ["yy", 0, 2],
    ["z", 0, 1],
  ]);
  assert.equal(r.touchesChip, false);
});

test("inside one text node it is that node's slice", () => {
  const a = text("hello");
  const root = el("div", {}, [a]);
  assert.equal(covered(root, { startContainer: a, startOffset: 1, endContainer: a, endOffset: 3 }), "el");
});

test("a collapsed range covers nothing", () => {
  const a = text("hello");
  const root = el("div", {}, [a]);
  assert.equal(segmentsOf(root, { startContainer: a, startOffset: 2, endContainer: a, endOffset: 2 }).segments.length, 0);
  assert.equal(segmentsOf(root, { startContainer: root, startOffset: 1, endContainer: root, endOffset: 1 }).segments.length, 0);
});

test("a range from outside the tree covers nothing rather than guessing", () => {
  const root = el("div", {}, [text("abc")]);
  const stray = text("zzz");
  const r = segmentsOf(root, { startContainer: stray, startOffset: 0, endContainer: stray, endOffset: 2 });
  assert.deepEqual(r, { segments: [], touchesChip: false });
});

test("for every pair of text positions, the segments are exactly the slice, minus chips", () => {
  // The invariant the wrapper relies on: nothing outside the selection is covered,
  // nothing inside it (apart from chips) is missed.
  const t1 = text("ab"), t2 = text("cde"), t3 = text("fg"), t4 = text("hi");
  const c = chip("$q$");
  const root = el("div", {}, [t1, el("b", {}, [t2, el("span", { "data-r": "gr" }, [t3])]), c, t4]);
  const nodes = [t1, t2, t3, c.childNodes[0], t4];
  const points: { node: T; off: number }[] = [];
  nodes.forEach((n) => {
    for (let o = 0; o <= (n.textContent ?? "").length; o++) points.push({ node: n, off: o });
  });
  const flat = nodes.map((n) => n.textContent ?? "");
  for (let i = 0; i < points.length; i++) {
    for (let j = i; j < points.length; j++) {
      const p = points[i], q = points[j];
      let expected = "";
      let sawChip = false;
      nodes.forEach((n, k) => {
        const from = n === p.node ? p.off : nodes.indexOf(n) > nodes.indexOf(p.node) ? 0 : -1;
        if (from < 0) return;
        const to = n === q.node ? q.off : nodes.indexOf(n) < nodes.indexOf(q.node) ? flat[k].length : -1;
        if (to < 0 || to <= from) return;
        if (n === c.childNodes[0]) sawChip = true;
        else expected += flat[k].slice(from, to);
      });
      const r = segmentsOf(root, { startContainer: p.node, startOffset: p.off, endContainer: q.node, endOffset: q.off });
      assert.equal(
        r.segments.map((s) => (s.node.textContent ?? "").slice(s.start, s.end)).join(""),
        expected,
        `from ${flat.indexOf(p.node.textContent ?? "")}:${p.off} to ${q.off}`,
      );
      assert.equal(r.touchesChip, sawChip);
      // At most one segment per text node: the link splitter cuts each node once, and a
      // second cut in the same node would use offsets the first has made stale.
      assert.equal(new Set(r.segments.map((s) => s.node)).size, r.segments.length);
    }
  }
});

test("buttonsFor: a bare caret in a link offers the url, not bold", () => {
  assert.deepEqual(buttonsFor({ collapsed: true, editing: true, linkCount: 1, touchesChip: false }), {
    format: "hidden", link: "edit", remove: true,
  });
});

test("buttonsFor: a selection with a chip cannot be bolded, but can still be linked", () => {
  assert.deepEqual(buttonsFor({ collapsed: false, editing: false, linkCount: 0, touchesChip: true }), {
    format: "chip", link: "new", remove: false,
  });
});

test("buttonsFor: plain selection, and a selection touching a link, differ only in remove", () => {
  assert.deepEqual(buttonsFor({ collapsed: false, editing: false, linkCount: 0, touchesChip: false }), {
    format: "enabled", link: "new", remove: false,
  });
  assert.equal(buttonsFor({ collapsed: false, editing: false, linkCount: 2, touchesChip: false }).remove, true);
});
