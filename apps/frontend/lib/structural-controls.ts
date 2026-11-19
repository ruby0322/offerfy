/**
 * Which structural controls the fields tab offers, and what a delete would take with it.
 *
 * Pure, like `structured-fields.ts`: the panel has no component test runner, so every
 * decision that can be a function is one. Nothing here sends anything; it reads a
 * projection and answers "may this button exist" and "what does it cost".
 *
 * A control that is hidden here is one the backend would refuse anyway
 * (`typst_intents.py`). The converse is not promised: see `canCopyItem`.
 */

import type { Projection, ProjectionBlock, ProjectionSection } from "./structured-fields";

/** A `show` block is the document's own rule; the backend refuses to delete it. */
const isContent = (block: ProjectionBlock) => block.kind !== "show";

/**
 * `insert_section` appends a heading at the end and needs none to exist: deleting the
 * last section leaves a document with none, and it must be possible to add one back.
 * Offered whenever the document has anything to lay a form out from. No guess is made
 * from `= ` / `=== ` headings about another convention: a document that never had `==`
 * sections and one that lost them to a delete look the same, and refusing the second
 * strands the person with no way back.
 */
export function canInsertSection(projection: Projection): boolean {
  return projection.sections.length > 0;
}

/**
 * Whether `insert_item` is offered for this section: any section with a heading.
 *
 * This is **necessary, not sufficient**, and deliberately so. The backend refuses for
 * four different reasons the projection cannot see from here -- the last block is a call
 * holding a positional string or `[...]` block (`_has_positional`, and
 * `report.skipped_args["(positional)"]` is a document-wide count with no block id), the
 * template has no emitter, its entries put fields where the form cannot reach them, or
 * there is nothing to copy and no emitter either. So the panel offers the button and
 * says which of those it was when the refusal comes; it does not guess.
 *
 * An *empty* section is the case the emitter fallback exists for (`_insert_first_item`),
 * so requiring a block to copy hid the button exactly where it was the only way in: a
 * person whose résumé has an empty `== Skills` could add nothing to it, only delete the
 * whole section.
 */
export function canCopyItem(section: ProjectionSection): boolean {
  // The preamble (no heading) holds the document's header, which is not a list of
  // entries; the backend would copy its last block, and nobody wants that.
  return section.titleUnit !== null;
}

/** `insert_bullet` adds to an item (a call), never to a bullet or a bare paragraph. */
export function canAddBullet(block: ProjectionBlock): boolean {
  return block.kind === "comp";
}

export function canDeleteBlock(block: ProjectionBlock): boolean {
  return isContent(block);
}

/**
 * Which way a section can move. `move_section` swaps with the neighbour, and refuses
 * the preamble and any move past it or off either end.
 */
export function sectionMoves(
  projection: Projection,
  sectionId: string,
): { up: boolean; down: boolean } {
  const i = projection.sections.findIndex((section) => section.id === sectionId);
  if (i < 0 || projection.sections[i].titleUnit === null) return { up: false, down: false };
  const titled = (j: number) =>
    j >= 0 && j < projection.sections.length && projection.sections[j].titleUnit !== null;
  return { up: titled(i - 1), down: titled(i + 1) };
}

/** A string's length in code points: every offset in this system is one, not a UTF-16 unit. */
export function codePoints(text: string): number {
  return Array.from(text).length;
}

export type DeleteImpact = {
  /** Editable values that go: string arguments and lines, bullets included. */
  units: number;
  /** Their characters, in code points. */
  chars: number;
  /** The block is the last real entry of a titled section, so the heading may go too. */
  lastInSection: boolean;
};

/** Every unit id inside a block: its arguments, its own line, its body, its bullets. */
export function blockUnitIds(block: ProjectionBlock): string[] {
  const ids: string[] = [];
  collect(block, ids);
  return ids;
}

function collect(block: ProjectionBlock, ids: string[]): void {
  ids.push(...block.args);
  if (block.unit) ids.push(block.unit);
  ids.push(...block.bodyUnits);
  for (const bullet of block.bullets) collect(bullet, ids);
}

/**
 * What deleting this block removes, counted as the content the person would miss.
 *
 * The span counted is **the units' values** -- the text in the fields, not the block's
 * `outer` span, which deliberately includes the trailing newline and blank lines and so
 * overstates the loss (and offsets are not in the frontend's types at all). Layout
 * settings (`style`) are left out: they are not something a person typed, and a name
 * bound by a `#let` outside the call survives the delete anyway.
 */
export function deleteImpact(
  projection: Projection,
  section: ProjectionSection,
  block: ProjectionBlock,
): DeleteImpact {
  const ids: string[] = [];
  collect(block, ids);
  let units = 0;
  let chars = 0;
  for (const id of new Set(ids)) {
    const unit = projection.units[id];
    if (!unit || unit.style) continue;
    units += 1;
    chars += codePoints(unit.value);
  }
  const real = section.blocks.filter(isContent);
  const lastInSection =
    section.titleUnit !== null && real.length === 1 && real[0].id === block.id;
  return { units, chars, lastInSection };
}

/**
 * A short name for a block, so a screen reader hearing a list of delete buttons can tell
 * them apart: its first content value, else its line, else its template name.
 */
export function blockLabel(projection: Projection, block: ProjectionBlock): string {
  const ids = [...block.args, ...(block.unit ? [block.unit] : []), ...block.bodyUnits];
  let text = "";
  for (const id of ids) {
    const unit = projection.units[id];
    if (unit && !unit.style && unit.value.trim() !== "") {
      text = unit.value.trim();
      break;
    }
  }
  if (text === "") text = block.name ?? "";
  const chars = Array.from(text);
  return chars.length > 24 ? `${chars.slice(0, 24).join("")}…` : text;
}

/** A top-level block or a bullet of one, with the section it sits in; null if gone. */
export function findBlock(
  projection: Projection,
  blockId: string,
): { section: ProjectionSection; block: ProjectionBlock } | null {
  for (const section of projection.sections) {
    for (const block of section.blocks) {
      if (block.id === blockId) return { section, block };
      for (const bullet of block.bullets) {
        if (bullet.id === blockId) return { section, block: bullet };
      }
    }
  }
  return null;
}

/**
 * What a structural control is pointing at, with everything that makes the id mean it.
 *
 * Ids are positional, so `b3` is only "this block" while nothing before it was inserted
 * or removed. The key therefore holds the id *and* the target's shape *and* how many
 * units and sections the whole document has: a structural edit changes the counts, and
 * a value edit changes none of it. That is the point -- a hash would change every time
 * a blurred field committed, which would drop an armed delete the moment the person
 * clicked it.
 *
 * Values are deliberately not in the key, for the same reason -- and that is a blind
 * spot, not a feature: two sibling entries of the same shape that swap places (a chat
 * turn, a history restore) produce the *same* key, because ids are positional counters
 * assigned in document order. No pure function of the projection can see that swap. The
 * panel therefore clears the arm whenever it adopts a projection it did not produce
 * itself (the fetch path), and on every structural success. The key only has to cover
 * what remains: a value edit, which must not disarm.
 */
function documentShape(projection: Projection): string {
  return `${Object.keys(projection.units).length}/${projection.sections.length}`;
}

function blockShape(projection: Projection, block: ProjectionBlock): string {
  // Names and argument keys are stable under a value edit and tell a `#work` from a
  // `#project` that happen to have the same number of fields.
  const keys = block.args.map((id) => projection.units[id]?.key ?? "").join(",");
  return [
    block.id,
    block.kind,
    block.name ?? "",
    block.args.join(","),
    keys,
    block.unit ?? "",
    block.bodyUnits.join(","),
    `[${block.bullets.map((bullet) => blockShape(projection, bullet)).join(";")}]`,
  ].join("|");
}

/** The key for a block or bullet, or null when no such block exists. */
export function blockKey(projection: Projection, blockId: string): string | null {
  const found = findBlock(projection, blockId);
  return found ? `${blockShape(projection, found.block)}@${documentShape(projection)}` : null;
}

/** The key for a section, or null when no such section exists. */
export function sectionKey(projection: Projection, sectionId: string): string | null {
  const section = projection.sections.find((x) => x.id === sectionId);
  if (!section) return null;
  const blocks = section.blocks.map((b) => blockShape(projection, b)).join(";");
  return `${section.id}|${section.titleUnit ?? ""}|${blocks}@${documentShape(projection)}`;
}

/**
 * Whether an armed delete still points at this target under the key it was armed with.
 *
 * The one test every drawing of the control and every highlight of it has to make, so it
 * is made in one place: a target whose key has moved on is not armed any more, and
 * neither is one whose key cannot be computed at all (the block is gone).
 */
export function armValid<T extends { target: string; key: string }>(
  armed: T | null,
  target: string,
  key: string | null,
): armed is T {
  return armed !== null && key !== null && armed.target === target && armed.key === key;
}

/**
 * The arm to keep when a projection is adopted.
 *
 * `armValid` re-checks the key on every render, and that covers everything a pure
 * function of the projection can see -- except the one case it cannot see at all: two
 * sibling entries of the same shape swapping places (a chat turn, a history restore)
 * produce an *identical* key, because ids are positional counters assigned in document
 * order. So the key is not the guard there; `produced` is. A projection this panel's own
 * value commit returned keeps the arm (disarming on every blurred field would drop it
 * the moment the person reached for it), and one that came from anywhere else drops it,
 * because the id it holds may now name the entry beside the one on screen.
 */
export function armAfterAdopt<T>(
  armed: T | null,
  adopted: { produced: boolean },
): T | null {
  return adopted.produced ? armed : null;
}

/** The key of a document as a whole, for the intents that name no target. */
export function documentKey(projection: Projection): string {
  return documentShape(projection);
}

/**
 * The whole document's layout: which unit id sits where. It changes when the unit set
 * does (a block added or removed, a section moved onto different ids) and not when a
 * value does. A row holding a draft from before such a change is holding a draft for an
 * id that may now name another part of the document, and drops it; see `StructuredPanel`.
 */
export function layoutKey(projection: Projection): string {
  const sections = projection.sections.map((section) => {
    const blocks = section.blocks.map((b) => blockShape(projection, b)).join(";");
    return `${section.id}|${section.titleUnit ?? ""}|${blocks}`;
  });
  return `${documentShape(projection)}#${sections.join("/")}`;
}

/**
 * A section can be deleted whole when it has a heading: the backend refuses only the
 * preamble. The only headed section may go too, as deleting its last item already can;
 * `insert_section` needs no heading to exist, so nothing is stranded.
 */
export function canDeleteSection(projection: Projection, sectionId: string): boolean {
  const section = projection.sections.find((x) => x.id === sectionId);
  return section !== undefined && section.titleUnit !== null;
}

/**
 * What deleting a whole section removes: its title and every content value in it, in
 * code points -- the same count `deleteImpact` makes for a block, over all of them.
 */
export function sectionDeleteImpact(
  projection: Projection,
  section: ProjectionSection,
): { units: number; chars: number } {
  const ids: string[] = [];
  if (section.titleUnit) ids.push(section.titleUnit);
  for (const block of section.blocks) collect(block, ids);
  let units = 0;
  let chars = 0;
  for (const id of new Set(ids)) {
    const unit = projection.units[id];
    if (!unit || unit.style) continue;
    units += 1;
    chars += codePoints(unit.value);
  }
  return { units, chars };
}
