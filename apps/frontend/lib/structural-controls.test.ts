import assert from "node:assert/strict";
import test from "node:test";

import {
  armAfterAdopt,
  armValid,
  blockKey,
  blockLabel,
  canAddBullet,
  canCopyItem,
  canDeleteSection,
  layoutKey,
  sectionDeleteImpact,
  canDeleteBlock,
  canInsertSection,
  codePoints,
  deleteImpact,
  documentKey,
  findBlock,
  sectionKey,
  sectionMoves,
} from "./structural-controls";
import type {
  Projection,
  ProjectionBlock,
  ProjectionSection,
  ProjectionUnit,
} from "./structured-fields";

const u = (id: string, value: string, over: Partial<ProjectionUnit> = {}): ProjectionUnit => ({
  id,
  kind: "line",
  value,
  editable: true,
  key: null,
  style: false,
  role: "bullet",
  marks: [],
  opaque: [],
  ...over,
});

const b = (id: string, over: Partial<ProjectionBlock> = {}): ProjectionBlock => ({
  id,
  kind: "comp",
  name: null,
  args: [],
  unit: null,
  bullets: [],
  bodyUnits: [],
  ...over,
});

const s = (
  id: string,
  titleUnit: string | null,
  blocks: ProjectionBlock[],
): ProjectionSection => ({ id, titleUnit, blocks });

function proj(sections: ProjectionSection[], units: ProjectionUnit[]): Projection {
  return {
    hash: "h1",
    tier: "A",
    sections,
    units: Object.fromEntries(units.map((x) => [x.id, x])),
    report: { structures: {}, skippedArgs: {}, lints: [] },
  };
}

test("insert_section needs no heading, only something to lay a form out from", () => {
  // deleting the last section leaves a preamble only; it must be possible to add one back
  assert.equal(canInsertSection(proj([s("s0", null, [])], [])), true);
  assert.equal(canInsertSection(proj([], [])), false);
  assert.equal(canInsertSection(proj([s("s0", null, []), s("s1", "u1", [])], [])), true);
  // `= ` / `===` headings and no `== ` at all: still offered, the backend appends
  const other = proj([s("s0", null, [])], []);
  other.report.structures["= heading"] = 1;
  assert.equal(canInsertSection(other), true);
  // ...but a `= Name` title above real `== ` sections is an ordinary résumé
  const titled = proj([s("s0", null, []), s("s1", "u1", [])], []);
  titled.report.structures["= heading"] = 1;
  assert.equal(canInsertSection(titled), true);
});

test("insert_item is offered on any headed section, empty ones included", () => {
  // An empty section is the only case `_insert_first_item`'s emitter fallback exists
  // for, so requiring a block to copy hid the button exactly where it was the one way
  // in: an empty `== Skills` could then only be deleted whole.
  assert.equal(canCopyItem(s("s1", "u1", [])), true);
  assert.equal(canCopyItem(s("s1", "u1", [b("b1", { kind: "show" })])), true);
  assert.equal(canCopyItem(s("s1", "u1", [b("b1", { kind: "show" }), b("b2")])), true);
  // the preamble is not a list of entries
  assert.equal(canCopyItem(s("s0", null, [b("b2")])), false);
});

test("add bullet only on a call; delete never on the show rule", () => {
  assert.equal(canAddBullet(b("b1")), true);
  assert.equal(canAddBullet(b("b1", { kind: "bullet" })), false);
  assert.equal(canAddBullet(b("b1", { kind: "para" })), false);
  assert.equal(canDeleteBlock(b("b1", { kind: "show" })), false);
  assert.equal(canDeleteBlock(b("b1", { kind: "para" })), true);
});

test("sections move only among titled neighbours", () => {
  const p = proj(
    [s("s1", null, []), s("s2", "u1", []), s("s3", "u2", []), s("s4", "u3", [])],
    [],
  );
  assert.deepEqual(sectionMoves(p, "s1"), { up: false, down: false });
  assert.deepEqual(sectionMoves(p, "s2"), { up: false, down: true });
  assert.deepEqual(sectionMoves(p, "s3"), { up: true, down: true });
  assert.deepEqual(sectionMoves(p, "s4"), { up: true, down: false });
  assert.deepEqual(sectionMoves(p, "nope"), { up: false, down: false });
});

test("characters are code points, not UTF-16 units", () => {
  assert.equal(codePoints("履歷"), 2);
  assert.equal(codePoints("a😀b"), 3); // "a😀b".length is 4
});

test("delete impact counts content values, bullets included, once each", () => {
  const bullet = b("b2", { kind: "bullet", unit: "u4" });
  const item = b("b1", {
    args: ["u1", "u1", "u2", "u9"], // u1 repeated (one #let bound twice), u9 missing
    bodyUnits: ["u3"],
    bullets: [bullet],
  });
  const section = s("s1", "u0", [item, b("b3")]);
  const p = proj(
    [section],
    [
      u("u1", "公司", { kind: "arg", key: "company", role: null }),
      u("u2", "px", { kind: "arg", key: "size", role: null, style: true }),
      u("u3", "做了😀"),
      u("u4", "要點"),
    ],
  );
  // u2 is a layout setting, u9 does not exist: neither is counted.
  assert.deepEqual(deleteImpact(p, section, item), {
    units: 3,
    chars: 2 + 3 + 2,
    lastInSection: false,
  });
  assert.deepEqual(deleteImpact(p, section, bullet), { units: 1, chars: 2, lastInSection: false });
});

test("last real entry of a titled section is flagged; a preamble's never is", () => {
  const only = b("b1", { unit: "u1", kind: "para" });
  const titled = s("s1", "u0", [b("b0", { kind: "show" }), only]);
  const p = proj([titled], [u("u1", "x")]);
  assert.equal(deleteImpact(p, titled, only).lastInSection, true);
  const two = s("s1", "u0", [only, b("b2")]);
  assert.equal(deleteImpact(p, two, only).lastInSection, false);
  const pre = s("s1", null, [only]);
  assert.equal(deleteImpact(p, pre, only).lastInSection, false);
});

test("findBlock reaches bullets and returns null for a gone id", () => {
  const bullet = b("b2", { kind: "bullet" });
  const section = s("s1", null, [b("b1", { bullets: [bullet] })]);
  const p = proj([section], []);
  assert.equal(findBlock(p, "b2")?.block, bullet);
  assert.equal(findBlock(p, "b1")?.section, section);
  assert.equal(findBlock(p, "b9"), null);
});

test("a key survives a value edit and dies on any structural one", () => {
  const item = b("b1", { args: ["u1"], bullets: [b("b2", { kind: "bullet", unit: "u2" })] });
  const section = s("s1", "u0", [item]);
  const base = proj([section], [u("u0", "T"), u("u1", "a"), u("u2", "b")]);
  const key = blockKey(base, "b1");
  assert.notEqual(key, null);
  // a value edit: new hash, new text, same shape
  const edited = { ...base, hash: "h2", units: { ...base.units, u1: u("u1", "changed") } };
  assert.equal(blockKey(edited, "b1"), key);
  // a bullet added inside the block: the block's own shape changes
  const more = proj(
    [s("s1", "u0", [{ ...item, bullets: [...item.bullets, b("b3", { kind: "bullet", unit: "u3" })] }])],
    [u("u0", "T"), u("u1", "a"), u("u2", "b"), u("u3", "c")],
  );
  assert.notEqual(blockKey(more, "b1"), key);
  // a block inserted *before* it elsewhere: same id, same shape, but the document grew
  const shifted = proj(
    [s("s0", "u9", [b("b0")]), section],
    [u("u0", "T"), u("u1", "a"), u("u2", "b"), u("u9", "N")],
  );
  assert.notEqual(blockKey(shifted, "b1"), key);
  // a unit added elsewhere in the same section (no new section, target's shape unchanged)
  const grown = proj(
    [s("s1", "u0", [b("b0", { unit: "u7", kind: "para" }), item])],
    [u("u0", "T"), u("u1", "a"), u("u2", "b"), u("u7", "n")],
  );
  assert.notEqual(blockKey(grown, "b1"), key);
  assert.equal(blockKey(base, "b9"), null);
});

test("layoutKey ignores values and sees any change to which id sits where", () => {
  const base = proj(
    [s("s1", "u0", [b("b1", { unit: "u1", kind: "para" })]), s("s2", "u2", [])],
    [u("u0", "A"), u("u1", "x"), u("u2", "B")],
  );
  const key = layoutKey(base);
  assert.equal(layoutKey({ ...base, hash: "h2", units: { ...base.units, u1: u("u1", "y") } }), key);
  // Two same-shaped entries swap places, as the parser would emit it: ids are counters
  // assigned in document order, so every id, block and shape is *identical* and only the
  // values differ.
  const pair = (first: string, second: string) =>
    proj(
      [
        s("s1", "u0", [
          b("s1i0", { name: "work", args: ["u1"] }),
          b("s1i1", { name: "work", args: ["u2"] }),
        ]),
      ],
      [
        u("u0", "Experience"),
        u("u1", first, { kind: "arg", key: "company", role: null }),
        u("u2", second, { kind: "arg", key: "company", role: null }),
      ],
    );
  // This is the blind spot, pinned so nobody mistakes the key for a guard against it:
  // the layout, and every block key, are equal across the swap. Closing it is the
  // panel's job (it clears an armed delete and bumps the row epoch whenever it adopts a
  // projection it did not produce), and a pure function of the projection cannot.
  assert.equal(layoutKey(pair("Meta", "Google")), layoutKey(pair("Google", "Meta")));
  assert.equal(
    blockKey(pair("Meta", "Google"), "s1i1"),
    blockKey(pair("Google", "Meta"), "s1i1"),
  );
  // ...whereas a different template with the same field count is told apart
  const other = pair("Meta", "Google");
  other.sections[0].blocks[1] = { ...other.sections[0].blocks[1], name: "project" };
  assert.notEqual(blockKey(other, "s1i1"), blockKey(pair("Meta", "Google"), "s1i1"));
  // a block removed: the unit set shrinks
  const fewer = proj([s("s1", "u0", []), s("s2", "u1", [])], [u("u0", "A"), u("u1", "B")]);
  assert.notEqual(layoutKey(fewer), key);
});

test("a section is deletable when it has a heading, even the only one", () => {
  const two = proj([s("s0", null, []), s("s1", "u1", []), s("s2", "u2", [])], []);
  assert.equal(canDeleteSection(two, "s0"), false);
  assert.equal(canDeleteSection(two, "s1"), true);
  assert.equal(canDeleteSection(two, "s9"), false);
  const one = proj([s("s0", null, []), s("s1", "u1", [])], []);
  assert.equal(canDeleteSection(one, "s1"), true);
});

test("section delete impact counts the title and every block, in code points", () => {
  const item = b("b1", { args: ["u2"], bullets: [b("b2", { kind: "bullet", unit: "u3" })] });
  const section = s("s1", "u1", [item, b("b3", { unit: "u4", kind: "para" })]);
  const p = proj(
    [section],
    [
      u("u1", "經歷"),
      u("u2", "公司", { kind: "arg", key: "company", role: null }),
      u("u3", "做了😀"),
      u("u4", "x"),
      u("u9", "elsewhere"),
    ],
  );
  assert.deepEqual(sectionDeleteImpact(p, section), { units: 4, chars: 2 + 2 + 3 + 1 });
});

test("a block label names the block for a screen reader", () => {
  const item = b("b1", { name: "work", args: ["u1", "u2"] });
  const p = proj(
    [s("s1", "u0", [item])],
    [
      u("u0", "T"),
      u("u1", "", { kind: "arg", key: "title", role: null }),
      u("u2", "A".repeat(40), { kind: "arg", key: "company", role: null }),
    ],
  );
  assert.equal(blockLabel(p, item), "A".repeat(24) + "…");
  assert.equal(blockLabel(p, b("b2", { name: "project" })), "project");
});

test("section and document keys follow the same rule", () => {
  const section = s("s1", "u0", [b("b1")]);
  const base = proj([section], [u("u0", "T")]);
  assert.notEqual(sectionKey(base, "s1"), null);
  assert.equal(sectionKey(base, "s2"), null);
  assert.equal(sectionKey({ ...base, hash: "h2" }, "s1"), sectionKey(base, "s1"));
  const grown = proj([section, s("s2", "u1", [])], [u("u0", "T"), u("u1", "U")]);
  assert.notEqual(sectionKey(grown, "s1"), sectionKey(base, "s1"));
  assert.notEqual(documentKey(grown), documentKey(base));
  assert.equal(documentKey({ ...base, hash: "h2" }), documentKey(base));
});

test("an arm survives this panel's own answer, never a projection from elsewhere", () => {
  // The case no key can see: two sibling entries of the same shape that swap places (a
  // chat turn, a history restore) produce an *identical* key, because ids are positional
  // counters assigned in document order. So an adopted projection this panel did not
  // produce disarms, and its own value commits' answers do not -- disarming on those
  // would drop the arm the moment a blurred field committed, which is while the person
  // is reaching for the confirm button.
  const arm = { target: "s1i0", key: "k", at: 1000 };
  assert.deepEqual(armAfterAdopt(arm, { produced: true }), arm);
  assert.equal(armAfterAdopt(arm, { produced: false }), null);
  assert.equal(armAfterAdopt(null, { produced: true }), null);
});

test("an arm is only armed for the target and key it was armed with", () => {
  const arm = { target: "s1i0", key: "shape@4/2", at: 1000 };
  assert.equal(armValid(arm, "s1i0", "shape@4/2"), true);
  assert.equal(armValid(arm, "s1i1", "shape@4/2"), false, "another target");
  assert.equal(armValid(arm, "s1i0", "shape@5/2"), false, "the key moved on");
  // No key at all: the block is gone from the projection, so nothing is armed.
  assert.equal(armValid(arm, "s1i0", null), false);
  assert.equal(armValid(null, "s1i0", "shape@4/2"), false);
});
