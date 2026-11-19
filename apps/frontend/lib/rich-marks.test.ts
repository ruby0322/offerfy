import assert from "node:assert/strict";
import test from "node:test";

import { domToMarks, marksToHtml, type Mark, type MarkNode } from "./rich-marks";

// Type-level proof, checked by `tsc --noEmit`: a live HTMLElement is a MarkNode, so the
// editor can hand `domToMarks` an element with no cast. If MarkNode ever asks for more
// than an element has, this line stops compiling.
const _elementIsMarkNode: HTMLElement extends MarkNode ? true : never = true;
void _elementIsMarkNode;

// A minimal HTML parser for the tags marksToHtml emits, producing plain MarkNode objects.
// There is no DOM in this harness; domToMarks only reads, so plain objects are enough.
type Tree = MarkNode & { childNodes: Tree[] };

function textNode(value: string): Tree {
  return { nodeType: 3, nodeName: "#text", textContent: value, childNodes: [] };
}

function element(name: string, attrs: Record<string, string>, children: Tree[]): Tree {
  return {
    nodeType: 1,
    nodeName: name.toUpperCase(),
    textContent: children.map((c) => c.textContent ?? "").join(""),
    childNodes: children,
    getAttribute: (key) => (key in attrs ? attrs[key] : null),
  };
}

function unescapeHtml(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&");
}

function parse(html: string): Tree {
  const root = element("div", {}, []);
  const stack: Tree[] = [root];
  const re = /<(\/?)([a-z]+)((?:\s+[a-z-]+(?:="[^"]*")?)*)\s*>|([^<]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[4] !== undefined) {
      top.childNodes.push(textNode(unescapeHtml(m[4])));
    } else if (m[1]) {
      stack.pop();
    } else {
      const attrs: Record<string, string> = {};
      for (const a of m[3].matchAll(/([a-z-]+)(?:="([^"]*)")?/g)) {
        attrs[a[1]] = a[2] === undefined ? "" : unescapeHtml(a[2]);
      }
      const node = element(m[2], attrs, []);
      top.childNodes.push(node);
      stack.push(node);
    }
  }
  // textContent of a parsed element is the concatenation of its descendants' text
  const fix = (n: Tree): string => {
    if (n.nodeType === 3) return n.textContent ?? "";
    n.textContent = n.childNodes.map(fix).join("");
    return n.textContent;
  };
  fix(root);
  return root;
}

test("marksToHtml nests a link inside bold", () => {
  const html = marksToHtml(
    "see docs now",
    [
      { kind: "strong", start: 0, end: 8 },
      { kind: "link", start: 4, end: 8, url: "https://x.dev/a?b=1&c=2" },
    ],
    [],
  );
  assert.equal(
    html,
    '<b>see <a data-url="https://x.dev/a?b=1&amp;c=2">docs</a></b> now',
  );
});

test("domToMarks is the inverse of marksToHtml", () => {
  const cases: Array<{ text: string; marks: Mark[]; opaque: string[] }> = [
    { text: "plain", marks: [], opaque: [] },
    {
      text: "a bold and italic b",
      marks: [
        { kind: "strong", start: 2, end: 6 },
        { kind: "emph", start: 11, end: 17 },
      ],
      opaque: [],
    },
    {
      text: "see docs now",
      marks: [
        { kind: "strong", start: 0, end: 8 },
        { kind: "link", start: 4, end: 8, url: "https://x.dev/?a=1&b=<2>" },
      ],
      opaque: [],
    },
    {
      text: "x < y && 1 > 0",
      marks: [{ kind: "emph", start: 0, end: 5 }],
      opaque: [],
    },
    {
      // the chip sits inside a mark; marks are in text coordinates, chip included
      text: "cost $x^2$ here",
      marks: [{ kind: "strong", start: 0, end: 10 }],
      opaque: ["$x^2$"],
    },
    {
      // same range twice: outer first, as the server emits them
      text: "both",
      marks: [
        { kind: "strong", start: 0, end: 4 },
        { kind: "emph", start: 0, end: 4 },
      ],
      opaque: [],
    },
  ];
  for (const c of cases) {
    const back = domToMarks(parse(marksToHtml(c.text, c.marks, c.opaque)));
    assert.deepEqual(back, { text: c.text, marks: c.marks });
  }
});

test("domToMarks keeps an opaque chip verbatim", () => {
  const html = marksToHtml("a #cite[x] b", [], ["#cite[x]"]);
  assert.match(
    html,
    /<span data-r="op" contenteditable="false" data-raw="#cite\[x\]">#cite\[x\]<\/span>/,
  );
  const back = domToMarks(parse(html));
  assert.equal(back.text, "a #cite[x] b");
  assert.deepEqual(back.marks, []);
  // the chip's visible text is not what is read back: data-raw wins
  const edited = element("div", {}, [
    textNode("a "),
    element("span", { "data-r": "op", "data-raw": "$raw$" }, [textNode("display")]),
  ]);
  assert.equal(domToMarks(edited).text, "a $raw$");
});

test("domToMarks strips a browser-inserted <div>", () => {
  const root = element("div", {}, [
    textNode("one "),
    element("div", {}, [textNode("two "), element("b", {}, [textNode("three")])]),
  ]);
  assert.deepEqual(domToMarks(root), {
    text: "one two three",
    marks: [{ kind: "strong", start: 8, end: 13 }],
  });
  const unknown = element("div", {}, [element("font", {}, [textNode("kept")])]);
  assert.deepEqual(domToMarks(unknown), { text: "kept", marks: [] });
});

test("mark offsets are code points, not UTF-16 units", () => {
  // One astral character BEFORE the mark: UTF-16 indexing is off by one here.
  const text = "🚀 shipped fast";
  const marks: Mark[] = [{ kind: "strong", start: 10, end: 14 }];
  assert.equal([...text].slice(10, 14).join(""), "fast");

  const html = marksToHtml(text, marks, []);
  assert.equal(html, "🚀 shipped <b>fast</b>");

  const back = domToMarks(parse(html));
  assert.equal(back.text, text);
  assert.deepEqual(back.marks, marks);
  const m = back.marks[0];
  assert.equal([...back.text].slice(m.start, m.end).join(""), "fast");

  // An astral character INSIDE the marked range and another after it.
  const t2 = "ab 🚀🚀 cd 🚀";
  const marks2: Mark[] = [{ kind: "emph", start: 3, end: 5 }];
  const html2 = marksToHtml(t2, marks2, []);
  assert.equal(html2, "ab <i>🚀🚀</i> cd 🚀");
  assert.deepEqual(domToMarks(parse(html2)), { text: t2, marks: marks2 });
});

test("repeated opaque runs become distinct chips and round-trip exactly", () => {
  const text = "a #linebreak() b #linebreak() c";
  const html = marksToHtml(text, [], ["#linebreak()", "#linebreak()"]);
  assert.equal(html.match(/data-raw="#linebreak\(\)"/g)?.length, 2);
  assert.deepEqual(domToMarks(parse(html)), { text, marks: [] });
  // a mark over the second run proves each chip sits at its own position
  const marks: Mark[] = [{ kind: "emph", start: 17, end: 29 }];
  const html2 = marksToHtml(text, marks, ["#linebreak()", "#linebreak()"]);
  assert.match(html2, /^a <span[^>]*>#linebreak\(\)<\/span> b <i><span[^>]*>#linebreak\(\)<\/span><\/i> c$/);
  assert.deepEqual(domToMarks(parse(html2)), { text, marks });
});

test("a mark boundary inside a chip demotes it to editable text", () => {
  // The server cannot emit this (an opaque run is atomic). Pinned so that if it ever
  // becomes reachable, this test is what says the chip has stopped being a chip.
  const text = "a $x^2$ b";
  const html = marksToHtml(text, [{ kind: "strong", start: 0, end: 4 }], ["$x^2$"]);
  assert.equal(html, "<b>a $x</b>^2$ b");
  assert.ok(!html.includes("data-raw"));
  assert.equal(domToMarks(parse(html)).text, text);
});

test("overlapping marks are truncated to the outer one", () => {
  const html = marksToHtml(
    "abcdefgh",
    [
      { kind: "strong", start: 0, end: 5 },
      { kind: "emph", start: 3, end: 8 },
    ],
    [],
  );
  assert.equal(html, "<b>abc<i>de</i></b>fgh");
  assert.deepEqual(domToMarks(parse(html)).marks, [
    { kind: "strong", start: 0, end: 5 },
    { kind: "emph", start: 3, end: 5 },
  ]);
});

test("domToMarks turns U+00A0 into a plain space", () => {
  const root = element("div", {}, [textNode("two  words ")]);
  assert.deepEqual(domToMarks(root), { text: "two  words ", marks: [] });
  // a chip's raw source is verbatim, NBSP included
  const chip = element("div", {}, [
    element("span", { "data-r": "op", "data-raw": "$a b$" }, []),
  ]);
  assert.equal(domToMarks(chip).text, "$a b$");
});

test("touching same-kind sibling marks round-trip to one mark, by design", () => {
  // The one input where domToMarks(marksToHtml(x)) is not the identity: merging tidies
  // the source (`*ab*`, not `*a**b*`) and renders the same.
  const html = marksToHtml(
    "ab",
    [
      { kind: "strong", start: 0, end: 1 },
      { kind: "strong", start: 1, end: 2 },
    ],
    [],
  );
  assert.deepEqual(domToMarks(parse(html)).marks, [{ kind: "strong", start: 0, end: 2 }]);
});

test("domToMarks does not merge same-kind marks across nesting levels", () => {
  // <b>a<i>b</i></b><i>c</i>: the second italic is outside the bold. Merging it into the
  // inner one would overhang the bold, and the clamp would drop the italic on "c".
  const root = element("div", {}, [
    element("b", {}, [textNode("a"), element("i", {}, [textNode("b")])]),
    element("i", {}, [textNode("c")]),
  ]);
  const marks: Mark[] = [
    { kind: "strong", start: 0, end: 2 },
    { kind: "emph", start: 1, end: 2 },
    { kind: "emph", start: 2, end: 3 },
  ];
  const back = domToMarks(root);
  assert.deepEqual(back, { text: "abc", marks });
  assert.equal(marksToHtml(back.text, back.marks, []), "<b>a<i>b</i></b><i>c</i>");
});

test("domToMarks merges touching same-kind marks only", () => {
  const touching = element("div", {}, [
    element("b", {}, [textNode("a")]),
    element("b", {}, [textNode("b")]),
  ]);
  assert.deepEqual(domToMarks(touching).marks, [{ kind: "strong", start: 0, end: 2 }]);

  const apart = element("div", {}, [
    element("b", {}, [textNode("a")]),
    textNode("-"),
    element("b", {}, [textNode("b")]),
  ]);
  assert.deepEqual(domToMarks(apart).marks, [
    { kind: "strong", start: 0, end: 1 },
    { kind: "strong", start: 2, end: 3 },
  ]);

  const link = (url: string, t: string) => element("a", { "data-url": url }, [textNode(t)]);
  const sameUrl = element("div", {}, [link("u", "a"), link("u", "b")]);
  assert.deepEqual(domToMarks(sameUrl).marks, [{ kind: "link", start: 0, end: 2, url: "u" }]);
  const otherUrl = element("div", {}, [link("u", "a"), link("v", "b")]);
  assert.equal(domToMarks(otherUrl).marks.length, 2);
});

// A `[group]` is the fourth mark kind the projection emits
// (`typst_projection._MARK_KINDS`), and `serialize_unit` writes it back as `[`…`]`. It
// must never take the `<a>` path: a group read back as a link carries no url, and
// `serialize_unit` refuses a blank one, so the whole line would go read-only.
test("a group is its own neutral wrapper, never a link", () => {
  const html = marksToHtml("a nested b", [{ kind: "group", start: 2, end: 8 }], []);
  assert.equal(html, 'a <span data-r="gr">nested</span> b');
  assert.ok(!html.includes("data-url"), `group drawn as a link: ${html}`);
  const back = domToMarks(parse(html));
  assert.deepEqual(back, { text: "a nested b", marks: [{ kind: "group", start: 2, end: 8 }] });
});

test("a group nests with the other marks in both directions", () => {
  const cases: Array<{ text: string; marks: Mark[] }> = [
    // `*[x] y*`: a group inside bold.
    {
      text: "x y",
      marks: [
        { kind: "strong", start: 0, end: 3 },
        { kind: "group", start: 0, end: 1 },
      ],
    },
    // `[*x* y]`: bold inside a group.
    {
      text: "x y",
      marks: [
        { kind: "group", start: 0, end: 3 },
        { kind: "strong", start: 0, end: 1 },
      ],
    },
    // `[#link("u")[x] y]`: a link inside a group.
    {
      text: "x y",
      marks: [
        { kind: "group", start: 0, end: 3 },
        { kind: "link", start: 0, end: 1, url: "u" },
      ],
    },
  ];
  for (const one of cases) {
    const html = marksToHtml(one.text, one.marks, []);
    assert.deepEqual(domToMarks(parse(html)), one, html);
  }
});

test("groups nested over the same range stay two groups", () => {
  // `[[x]]` is what the parser reads as two group marks over the same range (the
  // brackets are not part of `value`). Collapsing them the way `<b><b>` collapses would
  // write the line back as `[x]` and quietly drop a bracket pair.
  const marks: Mark[] = [
    { kind: "group", start: 0, end: 1 },
    { kind: "group", start: 0, end: 1 },
  ];
  const html = marksToHtml("x", marks, []);
  assert.equal(html, '<span data-r="gr"><span data-r="gr">x</span></span>');
  assert.deepEqual(domToMarks(parse(html)), { text: "x", marks });
});

test("a pasted hyperlink contributes its text and no link mark", () => {
  // The clipboard is the one thing that puts an <a> this module did not draw into the
  // surface. Reading its `href` would write a url into the person's source that no part
  // of the UI ever showed them -- a `javascript:` one included.
  const pasted = element("div", {}, [
    textNode("see "),
    element("a", { href: "javascript:alert(1)" }, [textNode("here")]),
  ]);
  assert.deepEqual(domToMarks(pasted), { text: "see here", marks: [] });

  // An <a> this module drew still round-trips, because it carries data-url.
  const ours = element("div", {}, [element("a", { "data-url": "https://x.test" }, [textNode("x")])]);
  assert.deepEqual(domToMarks(ours), {
    text: "x",
    marks: [{ kind: "link", start: 0, end: 1, url: "https://x.test" }],
  });
});

test("a mark kind this module does not know keeps its text and draws nothing", () => {
  // The branch `unitView`'s read-only guard rests on: a fifth kind from a newer API
  // must not be drawn as some other kind (a group drawn as a link cost a whole line its
  // editability), and must not take its text with it.
  const odd = [{ kind: "underline", start: 1, end: 3 }] as unknown as Mark[];
  const html = marksToHtml("abcd", odd, []);
  assert.equal(html, "abcd");
  assert.ok(!html.includes("<"), `drew a wrapper for an unknown kind: ${html}`);
  assert.deepEqual(domToMarks(parse(html)), { text: "abcd", marks: [] });
});

test("touching sibling groups are not merged", () => {
  // `[a][b]` and `[ab]` are different source. Bold merges by design (`*a**b*` is
  // `*ab*`); two adjacent groups are two groups.
  const marks: Mark[] = [
    { kind: "group", start: 0, end: 1 },
    { kind: "group", start: 1, end: 2 },
  ];
  const html = marksToHtml("ab", marks, []);
  assert.equal(html, '<span data-r="gr">a</span><span data-r="gr">b</span>');
  assert.deepEqual(domToMarks(parse(html)), { text: "ab", marks });
});

test("a pasted line break becomes one space, and a trailing one nothing", () => {
  // A unit is one line. `domToMarks` contributed no separator for a `<br>` or a block
  // element, so a two-line paste committed as "line onelineand two" -- and the other
  // half of the same paste, a literal newline, reached `serialize_unit`, which refused
  // it in English ("a line unit is one line").
  const br = element("div", {}, [
    textNode("line one"),
    element("br", {}, []),
    textNode("line two"),
  ]);
  assert.deepEqual(domToMarks(br), { text: "line one line two", marks: [] });

  const divs = element("div", {}, [
    element("div", {}, [textNode("line one")]),
    element("div", {}, [textNode("line two")]),
  ]);
  assert.deepEqual(domToMarks(divs), { text: "line one line two", marks: [] });

  // contenteditable leaves a `<br>` at the end of a line it has finished with, and a
  // space there would be committed as part of the person's own text.
  const trailing = element("div", {}, [textNode("line one"), element("br", {}, [])]);
  assert.deepEqual(domToMarks(trailing), { text: "line one", marks: [] });

  // Nor is a break doubled up with whitespace that is already there.
  const spaced = element("div", {}, [
    textNode("line one "),
    element("br", {}, []),
    textNode(" line two"),
  ]);
  assert.deepEqual(domToMarks(spaced), { text: "line one  line two", marks: [] });
});

test("a mark after a pasted line break starts where the text puts it", () => {
  const tree = element("div", {}, [
    textNode("before"),
    element("br", {}, []),
    element("b", {}, [textNode("after")]),
  ]);
  const got = domToMarks(tree);
  assert.equal(got.text, "before after");
  // 7, not 6: the space the break became is outside the bold, not inside it.
  assert.deepEqual(got.marks, [{ kind: "strong", start: 7, end: 12 }]);
  assert.equal(got.text.slice(7, 12), "after");
});
