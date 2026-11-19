import assert from "node:assert/strict";
import test from "node:test";

import {
  argIntent,
  argUnits,
  editRefusalOf,
  hasEditableFields,
  lineCommitDecision,
  lineIntent,
  parseProjection,
  unitView,
  type ProjectionMark,
  type ProjectionUnit,
} from "./structured-fields";

/** One unit as `GET /projection` serialises it, with every field present. */
function unit(over: Partial<ProjectionUnit> = {}): Record<string, unknown> {
  return {
    id: "u1",
    kind: "line",
    span: [10, 20],
    value: "hello",
    editable: true,
    key: null,
    style: false,
    role: "bullet",
    marks: [],
    opaque: [],
    escapes: [],
    ...over,
  };
}

const WIRE = {
  hash: "abc123",
  tier: "A",
  sections: [
    {
      id: "s1",
      title_unit: "u1",
      outer: [0, 40],
      body_start: 12,
      blocks: [
        {
          id: "s1i0",
          kind: "comp",
          name: "work",
          outer: [12, 40],
          content_end: 38,
          args: ["u2", "u3"],
          unit: null,
          bullets: [
            {
              id: "s1i0b0",
              kind: "bullet",
              name: null,
              outer: [30, 38],
              content_end: 38,
              args: [],
              unit: "u4",
              bullets: [],
              notes: [],
              body: null,
              body_units: [],
            },
          ],
          notes: [],
          body: null,
          body_units: [],
        },
      ],
    },
  ],
  units: {
    u1: unit({ id: "u1", value: "Experience", role: "heading" }),
    u2: unit({ id: "u2", kind: "arg", key: "title", role: null, value: "Engineer" }),
    u3: unit({ id: "u3", kind: "arg", key: "font", style: true, role: null, value: "Inter" }),
    u4: unit({
      id: "u4",
      value: "cut p95 to 240ms",
      marks: [{ kind: "strong", start: 0, end: 3, url: null }],
    }),
  },
  report: { structures: { "#grid": 2 }, skipped_args: {}, lints: ["x"] },
};

test("parseProjection keeps every field the panel reads", () => {
  const p = parseProjection(WIRE);
  assert.ok(p, "a server-shaped body must parse");
  assert.equal(p.hash, "abc123");
  assert.equal(p.tier, "A");
  assert.equal(p.sections.length, 1);
  assert.equal(p.sections[0].id, "s1");
  assert.equal(p.sections[0].titleUnit, "u1");
  const block = p.sections[0].blocks[0];
  assert.equal(block.id, "s1i0");
  assert.equal(block.kind, "comp");
  assert.equal(block.name, "work");
  assert.deepEqual(block.args, ["u2", "u3"]);
  assert.equal(block.unit, null);
  assert.deepEqual(block.bodyUnits, []);
  assert.equal(block.bullets.length, 1);
  assert.equal(block.bullets[0].unit, "u4");
  assert.equal(p.units.u3.style, true);
  assert.equal(p.units.u2.key, "title");
  assert.deepEqual(p.units.u4.marks, [{ kind: "strong", start: 0, end: 3, url: null }]);
  assert.deepEqual(p.report.structures, { "#grid": 2 });
});

test("a body that is not a projection does not parse as an empty one", () => {
  // The web app ships ahead of the API: this endpoint 404s, a proxy answers HTML, or
  // the shape changes. Every one of those must be distinguishable from a resume that
  // genuinely has no editable fields, so the parser refuses instead of emptying out.
  for (const bad of [
    null,
    undefined,
    "",
    "Not Found",
    42,
    [],
    {},
    { detail: "Not Found" },
    { ...WIRE, hash: 1 },
    { ...WIRE, units: [] },
    { ...WIRE, sections: {} },
    { ...WIRE, tier: null },
  ]) {
    assert.equal(parseProjection(bad), null, `parsed: ${JSON.stringify(bad)}`);
  }
});

test("a unit kind this build does not know is read-only, not a failed parse", () => {
  // Version skew runs API-ahead-of-web here, which is the whole reason Ruling 1 exists.
  // One new unit kind must cost that unit its form, not the whole tab.
  const p = parseProjection({
    ...WIRE,
    units: { ...WIRE.units, u4: unit({ id: "u4", kind: "table-cell" as "line" }) },
  });
  assert.ok(p, "an unknown unit kind must still parse");
  assert.equal(p.units.u4.kind, "other");
  assert.equal(p.units.u4.value, "hello");
  const view = unitView(p.units.u4);
  assert.equal(view.editable, false);
  assert.equal(view.editable === false && view.reason, "kind");
  // And it still gets an argument row rather than disappearing from the block.
  const rows = argUnits({ args: ["u4"] }, p.units);
  assert.deepEqual(
    rows.fields.map((u) => u.id),
    ["u4"],
  );
});

test("one malformed unit fails the whole parse rather than vanishing", () => {
  for (const bad of [
    { ...unit(), kind: 7 },
    { ...unit(), value: 7 },
    { ...unit(), editable: "yes" },
    { ...unit(), marks: [{ kind: "strong", start: "0", end: 3 }] },
    { ...unit(), opaque: [3] },
    // Filed under the wrong key: the key is what an intent names, so this would edit a
    // different part of the document.
    { ...unit(), id: "u9" },
    "u1",
  ]) {
    const p = parseProjection({ ...WIRE, units: { ...WIRE.units, u4: bad } });
    assert.equal(p, null, `parsed: ${JSON.stringify(bad)}`);
  }
});

test("a block or section the panel could not render fails the parse", () => {
  const sections = WIRE.sections;
  const broken = [
    { ...sections[0], blocks: "none" },
    { ...sections[0], id: null },
    { ...sections[0], title_unit: 3 },
    { ...sections[0], blocks: [{ ...sections[0].blocks[0], args: ["u2", 4] }] },
    { ...sections[0], blocks: [{ ...sections[0].blocks[0], bullets: null }] },
  ];
  for (const bad of broken) {
    assert.equal(
      parseProjection({ ...WIRE, sections: [bad] }),
      null,
      `parsed: ${JSON.stringify(bad)}`,
    );
  }
});

test("a mark kind the editor cannot render makes the unit read-only", () => {
  // The guard against a *fifth* kind arriving from a newer API: `marksToHtml` would
  // keep its text and draw no wrapper, so `domToMarks` could not read it back and a
  // commit would drop it from the source. The four known kinds are all drawable (see
  // the `group` case below); this is what keeps the next one from being lost.
  const odd = parseProjection({
    ...WIRE,
    units: {
      ...WIRE.units,
      u4: unit({
        id: "u4",
        marks: [
          { kind: "strong", start: 0, end: 2, url: null },
          { kind: "underline", start: 3, end: 5, url: null },
        ],
      }),
    },
  });
  assert.ok(odd);
  const view = unitView(odd.units.u4);
  assert.equal(view.editable, false);
  assert.equal(view.editable === false && view.reason, "marks");
  // The renderable marks survive; only the one it cannot draw is dropped.
  assert.deepEqual(view.marks, [{ kind: "strong", start: 0, end: 2 }]);
});

test("a group mark is editable, and keeps its range", () => {
  // `[...]` is the projection's fourth kind. It used to land in `marksToHtml`'s `<a>`
  // fallback and come back as a link with no url, which `serialize_unit` refuses -- so
  // every line with a group in it was shown read-only. It is a real wrapper now.
  const grouped = parseProjection({
    ...WIRE,
    units: {
      ...WIRE.units,
      u4: unit({
        id: "u4",
        marks: [
          { kind: "group", start: 0, end: 5, url: null },
          { kind: "emph", start: 1, end: 3, url: null },
        ],
      }),
    },
  });
  assert.ok(grouped);
  const view = unitView(grouped.units.u4);
  assert.equal(view.editable, true);
  assert.deepEqual(view.marks, [
    { kind: "group", start: 0, end: 5 },
    { kind: "emph", start: 1, end: 3 },
  ]);
});

test("a link inside a link is read-only, while sibling links stay editable", () => {
  // `#link("A")[the #link("B")[inner] tail]` is legal Typst. `marksToHtml` draws it as
  // a nested `<a>`, which the HTML parser is required to flatten into siblings, so
  // `domToMarks` reads the outer link as ending where the inner one starts -- and a
  // commit then wrote the outer link's tail out of it, on nothing more than a click
  // into the line and out again. No markup holds the shape, so it is not offered.
  const view = (marks: ProjectionMark[]) => {
    const p = parseProjection({
      ...WIRE,
      units: { ...WIRE.units, u4: unit({ id: "u4", marks }) },
    });
    assert.ok(p);
    return unitView(p.units.u4);
  };

  const nested = view([
    { kind: "link", start: 4, end: 18, url: "A" },
    { kind: "link", start: 8, end: 13, url: "B" },
  ]);
  assert.equal(nested.editable, false);
  assert.equal(nested.editable === false && nested.reason, "marks");
  // The marks still reach the surface, so the line is shown with its links drawn.
  assert.equal(nested.marks.length, 2);

  // Two links side by side are the common case and nest nothing.
  assert.equal(
    view([
      { kind: "link", start: 0, end: 4, url: "A" },
      { kind: "link", start: 4, end: 9, url: "B" },
    ]).editable,
    true,
  );
  // A link inside *bold* is the shape the fixture has, and HTML holds it fine.
  assert.equal(
    view([
      { kind: "strong", start: 0, end: 9, url: null },
      { kind: "link", start: 2, end: 6, url: "A" },
    ]).editable,
    true,
  );
});

test("the server's own read-only verdict reads as a serialize reason", () => {
  const view = unitView({
    ...(parseProjection(WIRE) as NonNullable<ReturnType<typeof parseProjection>>).units.u4,
    editable: false,
  });
  assert.equal(view.editable, false);
  assert.equal(view.editable === false && view.reason, "serialize");
  assert.deepEqual(view.marks, [{ kind: "strong", start: 0, end: 3 }]);
});

test("a link mark keeps its url, and an editable unit stays editable", () => {
  const p = parseProjection({
    ...WIRE,
    units: {
      ...WIRE.units,
      u4: unit({ id: "u4", marks: [{ kind: "link", start: 1, end: 4, url: "https://x.test" }] }),
    },
  });
  assert.ok(p);
  const view = unitView(p.units.u4);
  assert.equal(view.editable, true);
  assert.deepEqual(view.marks, [{ kind: "link", start: 1, end: 4, url: "https://x.test" }]);
});

test("argUnits splits style from content, keeps order, dedupes and drops strays", () => {
  const p = parseProjection(WIRE);
  assert.ok(p);
  // "u2" twice: one `#let` bound to two arguments of the same block yields the same
  // unit id twice, and a duplicate React key would drop a row.
  const rows = argUnits({ ...p.sections[0].blocks[0], args: ["u3", "u2", "u2", "u9"] }, p.units);
  assert.deepEqual(
    rows.fields.map((u) => u.id),
    ["u2"],
  );
  assert.deepEqual(
    rows.style.map((u) => u.id),
    ["u3"],
  );
  // A line unit listed as an argument is not an arg row.
  const mixed = argUnits({ ...p.sections[0].blocks[0], args: ["u1", "u2"] }, p.units);
  assert.deepEqual(
    mixed.fields.map((u) => u.id),
    ["u2"],
  );
});

test("a line commits only when it changed, and never commits an emptied line", () => {
  const was = { text: "hello", marks: [{ kind: "strong" as const, start: 0, end: 2 }] };
  assert.deepEqual(lineCommitDecision({ text: "hello!", marks: was.marks }, was), {
    send: true,
  });
  assert.deepEqual(lineCommitDecision(was, was), { send: false, reason: "unchanged" });
  // Same text, different formatting: still a change.
  assert.deepEqual(
    lineCommitDecision({ text: "hello", marks: [{ kind: "strong", start: 0, end: 3 }] }, was),
    { send: true },
  );
  assert.deepEqual(lineCommitDecision({ text: "hello", marks: [] }, was), { send: true });
  assert.deepEqual(
    lineCommitDecision({ text: "hello", marks: [{ kind: "emph", start: 0, end: 2 }] }, was),
    { send: true },
  );
  // A link's url is part of the mark.
  const linked = { text: "a", marks: [{ kind: "link" as const, start: 0, end: 1, url: "u" }] };
  assert.deepEqual(lineCommitDecision(linked, linked), { send: false, reason: "unchanged" });
  assert.deepEqual(
    lineCommitDecision(
      { text: "a", marks: [{ kind: "link", start: 0, end: 1, url: "v" }] },
      linked,
    ),
    { send: true },
  );
  // `set_line` refuses an empty line (it would stop being a unit); so do we, before
  // spending a request on it.
  for (const empty of ["", "   ", " ", "\t"]) {
    assert.deepEqual(lineCommitDecision({ text: empty, marks: [] }, was), {
      send: false,
      reason: "empty",
    });
  }
});

test("an intent carries the hash and the expect of the projection it is built from", () => {
  // Only that. **Which** projection the queue hands it -- the ordering property this
  // whole design rests on -- is `edit-queue.test.ts`'s job, because it is the queue
  // that decides, and a test here could not tell a fresh projection from a stale one.
  const before = parseProjection(WIRE);
  const afterA = parseProjection({
    ...WIRE,
    hash: "hash-after-a",
    units: { ...WIRE.units, u1: unit({ id: "u1", value: "A edited" }) },
  });
  assert.ok(before && afterA);

  const typed = { text: "cut p95 to 120ms", marks: [] };
  const stale = lineIntent(before, "u4", typed);
  const fresh = lineIntent(afterA, "u4", typed);
  assert.deepEqual(stale, {
    hash: "abc123",
    kind: "set_line",
    unit: "u4",
    expect: "cut p95 to 240ms",
    text: "cut p95 to 120ms",
    marks: [],
  });
  assert.deepEqual(fresh, { ...stale, hash: "hash-after-a" });
  assert.notEqual(fresh?.hash, stale?.hash, "the queue must not send a stale hash");
});

test("an intent is null when the projection it is built from already has the text", () => {
  // The dedupe itself. It only fires on a *fresh* projection, which is why the queue
  // publishing the answer in the same turn is load-bearing -- see `edit-queue.test.ts`,
  // "a commit the answer makes redundant is dropped".
  const landed = parseProjection({
    ...WIRE,
    hash: "hash-after-b",
    units: { ...WIRE.units, u4: unit({ id: "u4", value: "cut p95 to 120ms" }) },
  });
  assert.ok(landed);
  assert.equal(lineIntent(landed, "u4", { text: "cut p95 to 120ms", marks: [] }), null);
  // Marks still count as a change even when the text does not.
  assert.ok(
    lineIntent(landed, "u4", {
      text: "cut p95 to 120ms",
      marks: [{ kind: "strong", start: 0, end: 3 }],
    }),
  );
  assert.equal(argIntent(landed, "u2", "Engineer"), null);
  assert.ok(argIntent(landed, "u2", "Engineer II"));
});

test("an intent is never built for something the server would refuse", () => {
  const p = parseProjection({
    ...WIRE,
    units: {
      ...WIRE.units,
      u5: unit({ id: "u5", editable: false }),
      u6: unit({ id: "u6", kind: "weird" as "line" }),
      u7: unit({ id: "u7", kind: "arg", key: "title", role: null, editable: false }),
    },
  });
  assert.ok(p);
  const typed = { text: "anything", marks: [] };
  assert.equal(lineIntent(p, "u5", typed), null, "a read-only line");
  assert.equal(lineIntent(p, "u6", typed), null, "a unit kind we do not know");
  assert.equal(lineIntent(p, "u404", typed), null, "a unit that is gone");
  assert.equal(lineIntent(p, "u2", typed), null, "an arg addressed as a line");
  assert.equal(argIntent(p, "u7", "x"), null, "a read-only arg");
  assert.equal(argIntent(p, "u4", "x"), null, "a line addressed as an arg");
  // `apply_intent` refuses a value with a newline; do not spend a request on it.
  assert.equal(argIntent(p, "u2", "two\nlines"), null);
  assert.equal(argIntent(p, "u2", "two\rlines"), null);
});

test("editRefusalOf reads the code out of FastAPI's nested detail", () => {
  assert.deepEqual(editRefusalOf({ detail: { code: "stale_hash", detail: "It changed." } }), {
    code: "stale_hash",
    detail: "It changed.",
    reason: "",
  });
  assert.deepEqual(editRefusalOf({ detail: { code: "compile_failed" } }), {
    code: "compile_failed",
    detail: "",
    reason: "",
  });
  // `unsupported` covers four quite different refusals for `insert_item` alone, so the
  // server names which; the panel cannot word it in the person's language off one
  // English sentence, and showed the positional case's sentence for all four.
  assert.deepEqual(
    editRefusalOf({
      detail: { code: "unsupported", detail: "there is no emitter", reason: "no_emitter" },
    }),
    { code: "unsupported", detail: "there is no emitter", reason: "no_emitter" },
  );
  // A reason that is not a string is no reason, and never a crash on a refusal.
  assert.deepEqual(editRefusalOf({ detail: { code: "unsupported", reason: 7 } }), {
    code: "unsupported",
    detail: "",
    reason: "",
  });
  // A refusal from anywhere else in the app: FastAPI's own validation body, a detail
  // that is an object but carries no code, a bare message.
  for (const other of [
    null,
    undefined,
    "nope",
    { detail: "plain string" },
    { detail: { msg: "field required" } },
    { detail: [{ msg: "field required" }] },
    { code: "x" },
    {},
  ]) {
    assert.equal(editRefusalOf(other), null, `read a refusal out of ${JSON.stringify(other)}`);
  }
});

test("hasEditableFields separates 'nothing to edit' from a document with fields", () => {
  const p = parseProjection(WIRE);
  assert.ok(p);
  assert.equal(hasEditableFields(p), true);
  assert.equal(hasEditableFields({ ...p, tier: "C" }), false);
  assert.equal(hasEditableFields({ ...p, sections: [] }), false);
});

test("a pasted newline becomes a space instead of an English compiler message", () => {
  const p = parseProjection(WIRE);
  assert.ok(p);
  // `serialize_unit` refuses a newline ("a line unit is one line"), which the panel
  // showed verbatim: 「這項修改不支援：a line unit is one line」. A bullet copied out of
  // a job description is among the likeliest things this form meets.
  const got = lineIntent(p, "u4", { text: "line one\nline two", marks: [] });
  assert.equal(got?.text, "line one line two");
  assert.ok(!/[\r\n]/.test(got?.text ?? "\n"));

  // One character for one, so a mark offset still points at the words it did.
  const marked = lineIntent(p, "u4", {
    text: "first\r\nsecond",
    marks: [{ kind: "strong", start: 7, end: 13 }],
  });
  assert.equal(marked?.text, "first  second");
  assert.equal(Array.from(marked?.text ?? "").slice(7, 13).join(""), "second");

  // A newline on its own leaves whitespace, which `set_line` refuses to write; the
  // decision is made after the collapse, so nothing is sent and the panel says so.
  assert.equal(lineIntent(p, "u4", { text: "\n", marks: [] }), null);
  assert.equal(lineIntent(p, "u4", { text: "\r\n ", marks: [] }), null);
});
