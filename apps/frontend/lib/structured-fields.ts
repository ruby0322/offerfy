/**
 * The fields tab's pure half: the projection as the editor reads it, parsed
 * defensively, plus the small decisions the panel and `RichLine` make about it.
 *
 * No React, no DOM, no fetch, so `tsx --test` can exercise all of it. That matters
 * more than usual here: this repo has no component test runner (`AGENTS.md` §269), so
 * the panel itself has no automated gate and everything that can be a function should
 * be one.
 *
 * Offsets (`span`, `outer`, `content_end`) are deliberately **not** in these types.
 * The backend owns every byte position; the frontend addresses content by unit id and
 * would only be able to misuse an offset it kept.
 */

import type { Mark } from "./rich-marks";

/** A mark exactly as the wire carries it: `kind` is a string, not a union. */
export type ProjectionMark = {
  kind: string;
  start: number;
  end: number;
  url: string | null;
};

export type ProjectionUnit = {
  id: string;
  /**
   * `arg` is a string argument, `line` a line of content. `other` is a kind this build
   * does not know: an API ahead of the web app, which is the direction version skew
   * actually runs here. It is shown, read-only, rather than failing the whole parse.
   */
  kind: "arg" | "line" | "other";
  value: string;
  editable: boolean;
  /** An `arg`'s argument name (`title`, `font`); null on a `line`. */
  key: string | null;
  /** A layout setting rather than content, grouped apart in the form. */
  style: boolean;
  /** A `line`'s role: `heading`, `bullet`, `para`; null on an `arg`. */
  role: string | null;
  marks: ProjectionMark[];
  /** Raw source runs (math, labels, comments) carried inside `value`, in order. */
  opaque: string[];
};

export type ProjectionBlock = {
  id: string;
  kind: string;
  name: string | null;
  /** Unit ids of this block's string arguments, in the source's order. */
  args: string[];
  /** The one line unit of a bare paragraph or bullet block. */
  unit: string | null;
  bullets: ProjectionBlock[];
  /** Line units inside a `#call(...)[...]` content body. */
  bodyUnits: string[];
};

export type ProjectionSection = {
  id: string;
  titleUnit: string | null;
  blocks: ProjectionBlock[];
};

export type ProjectionReport = {
  structures: Record<string, number>;
  skippedArgs: Record<string, number>;
  lints: string[];
};

export type Projection = {
  hash: string;
  /** "A" every unit editable, "B" some read-only, "C" nothing editable. */
  tier: string;
  sections: ProjectionSection[];
  units: Record<string, ProjectionUnit>;
  report: ProjectionReport;
};

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const out: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") return null;
    out.push(entry);
  }
  return out;
}

function nullableString(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return null;
  return typeof value === "string" ? value : undefined;
}

function counts(value: unknown): Record<string, number> {
  const rec = record(value);
  if (!rec) return {};
  const out: Record<string, number> = {};
  for (const [key, count] of Object.entries(rec)) {
    if (typeof count === "number" && Number.isFinite(count)) out[key] = count;
  }
  return out;
}

function parseMark(value: unknown): ProjectionMark | null {
  const rec = record(value);
  if (!rec) return null;
  if (typeof rec.kind !== "string") return null;
  if (typeof rec.start !== "number" || !Number.isFinite(rec.start)) return null;
  if (typeof rec.end !== "number" || !Number.isFinite(rec.end)) return null;
  const url = nullableString(rec.url);
  if (url === undefined) return null;
  return { kind: rec.kind, start: rec.start, end: rec.end, url };
}

function parseUnit(id: string, value: unknown): ProjectionUnit | null {
  const rec = record(value);
  if (!rec) return null;
  // The map key is what the panel sends as an intent's `unit`, so a row filed under the
  // wrong key would edit a different part of the document. Refuse rather than guess.
  if (rec.id !== id) return null;
  // A kind this build does not know is version skew, not a broken body: keep the unit
  // and let `unitView` show it read-only. A kind that is not even a string is a broken
  // body.
  if (typeof rec.kind !== "string") return null;
  const kind = rec.kind === "arg" || rec.kind === "line" ? rec.kind : "other";
  if (typeof rec.value !== "string") return null;
  if (typeof rec.editable !== "boolean") return null;
  const key = nullableString(rec.key);
  const role = nullableString(rec.role);
  if (key === undefined || role === undefined) return null;
  if (!Array.isArray(rec.marks)) return null;
  const marks: ProjectionMark[] = [];
  for (const entry of rec.marks) {
    const mark = parseMark(entry);
    if (!mark) return null;
    marks.push(mark);
  }
  const opaque = stringList(rec.opaque);
  if (!opaque) return null;
  return {
    id,
    kind,
    value: rec.value,
    editable: rec.editable,
    key,
    style: rec.style === true,
    role,
    marks,
    opaque,
  };
}

function parseBlock(value: unknown): ProjectionBlock | null {
  const rec = record(value);
  if (!rec) return null;
  if (typeof rec.id !== "string" || typeof rec.kind !== "string") return null;
  const name = nullableString(rec.name);
  const unit = nullableString(rec.unit);
  if (name === undefined || unit === undefined) return null;
  const args = stringList(rec.args);
  const bodyUnits = stringList(rec.body_units);
  if (!args || !bodyUnits) return null;
  if (!Array.isArray(rec.bullets)) return null;
  const bullets: ProjectionBlock[] = [];
  for (const entry of rec.bullets) {
    const bullet = parseBlock(entry);
    if (!bullet) return null;
    bullets.push(bullet);
  }
  return { id: rec.id, kind: rec.kind, name, args, unit, bullets, bodyUnits };
}

function parseSection(value: unknown): ProjectionSection | null {
  const rec = record(value);
  if (!rec) return null;
  if (typeof rec.id !== "string") return null;
  const titleUnit = nullableString(rec.title_unit);
  if (titleUnit === undefined) return null;
  if (!Array.isArray(rec.blocks)) return null;
  const blocks: ProjectionBlock[] = [];
  for (const entry of rec.blocks) {
    const block = parseBlock(entry);
    if (!block) return null;
    blocks.push(block);
  }
  return { id: rec.id, titleUnit, blocks };
}

/**
 * The projection, or null when the body is not one.
 *
 * Null is the important half. `parsePreviewPages` answers a malformed body with an
 * empty preview, which is right there -- no pages, no picture -- but this repo has
 * shipped the other version of that bug: the web app deploys ahead of the API, the new
 * endpoint 404s, a null-safe parser turns it into an empty object, and the person is
 * told their data is empty when the truth is that the server is older than the page.
 * A projection that parses to nothing would read as "your resume has no editable
 * fields", so the caller throws instead and the panel renders a different state.
 *
 * The one deliberate exception is `report`, which nothing renders yet: a change to its
 * shape costs diagnostics, and must not cost the person their fields.
 */
export function parseProjection(data: unknown): Projection | null {
  const rec = record(data);
  if (!rec) return null;
  if (typeof rec.hash !== "string" || typeof rec.tier !== "string") return null;
  if (!Array.isArray(rec.sections)) return null;
  const unitRecords = record(rec.units);
  if (!unitRecords) return null;
  const units: Record<string, ProjectionUnit> = {};
  for (const [id, value] of Object.entries(unitRecords)) {
    const parsed = parseUnit(id, value);
    if (!parsed) return null;
    units[id] = parsed;
  }
  const sections: ProjectionSection[] = [];
  for (const entry of rec.sections) {
    const section = parseSection(entry);
    if (!section) return null;
    sections.push(section);
  }
  const report = record(rec.report) ?? {};
  return {
    hash: rec.hash,
    tier: rec.tier,
    sections,
    units,
    report: {
      structures: counts(report.structures),
      skippedArgs: counts(report.skipped_args),
      lints: stringList(report.lints) ?? [],
    },
  };
}

/** Whether this document has anything for the form to show at all. */
export function hasEditableFields(projection: Projection): boolean {
  // Tier C *is* "no editable unit" (`typst_projection._build`), and a document with no
  // section has nothing to lay a form out from either.
  return projection.tier !== "C" && projection.sections.length > 0;
}

/** The four kinds `marksToHtml` draws and `domToMarks` reads back. */
function renderableKind(kind: string): Mark["kind"] | null {
  return kind === "strong" || kind === "emph" || kind === "link" || kind === "group"
    ? kind
    : null;
}

/**
 * Whether one link encloses another.
 *
 * `#link("A")[the #link("B")[inner] tail]` is legal Typst, and `marksToHtml` draws it
 * as a nested `<a>` -- which the HTML parser is required to flatten into siblings, so
 * `domToMarks` reads the outer link ending where the inner one begins and a commit
 * would write the outer link's tail out of it. No markup can hold the shape, so the
 * line is shown as it is and not offered for editing.
 */
function nestsALink(marks: Mark[]): boolean {
  const links = marks.filter((m) => m.kind === "link" && m.end > m.start);
  return links.some((a) =>
    links.some((b) => b !== a && b.start >= a.start && b.end <= a.end),
  );
}

export type UnitView =
  | { editable: true; marks: Mark[] }
  /**
   * `serialize`: the server cannot write this unit back. `marks`: its formatting is
   * beyond this surface -- a mark kind this build cannot draw (version skew, one unit at
   * a time), or a shape no HTML can hold, such as a link inside a link. `kind`: the unit
   * itself is of a kind this build has no form for.
   */
  | { editable: false; reason: "serialize" | "marks" | "kind"; marks: Mark[] };

/**
 * How to show one unit: the marks `marksToHtml` can draw, and whether it may be edited.
 *
 * All four kinds the projection emits are drawable, so this is the guard against a
 * *fifth* one arriving from a newer API. `marksToHtml` keeps such a mark's text and
 * draws no wrapper, so `domToMarks` could not read it back and a commit would silently
 * drop it from the source. Read-only instead, with the reason on screen.
 */
export function unitView(unit: ProjectionUnit): UnitView {
  const marks: Mark[] = [];
  let dropped = false;
  for (const mark of unit.marks) {
    const kind = renderableKind(mark.kind);
    if (!kind) {
      dropped = true;
      continue;
    }
    marks.push(
      mark.url === null
        ? { kind, start: mark.start, end: mark.end }
        : { kind, start: mark.start, end: mark.end, url: mark.url },
    );
  }
  if (unit.kind === "other") return { editable: false, reason: "kind", marks };
  if (!unit.editable) return { editable: false, reason: "serialize", marks };
  if (dropped || nestsALink(marks)) return { editable: false, reason: "marks", marks };
  return { editable: true, marks };
}

/** A block's string arguments as rows: content first, layout settings apart. */
export function argUnits(
  block: Pick<ProjectionBlock, "args">,
  units: Record<string, ProjectionUnit>,
): { fields: ProjectionUnit[]; style: ProjectionUnit[] } {
  const fields: ProjectionUnit[] = [];
  const style: ProjectionUnit[] = [];
  const seen = new Set<string>();
  for (const id of block.args) {
    // One `#let` bound to two arguments of the same call is one unit listed twice; a
    // duplicate key would drop a row.
    if (seen.has(id)) continue;
    seen.add(id);
    const unit = units[id];
    // A unit of an unknown kind still gets a row -- read-only, via `unitView` -- so a
    // newer API cannot make an argument disappear from the form. A `line` unit here
    // would be the wrong shape entirely and is left out.
    if (!unit || unit.kind === "line") continue;
    (unit.style ? style : fields).push(unit);
  }
  return { fields, style };
}

function sameMarks(a: Mark[], b: Mark[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((mark, i) => {
    const other = b[i];
    return (
      mark.kind === other.kind &&
      mark.start === other.start &&
      mark.end === other.end &&
      (mark.url ?? null) === (other.url ?? null)
    );
  });
}

export type LineCommit = { send: true } | { send: false; reason: "unchanged" | "empty" };

/**
 * Whether to spend a request on what the surface now holds.
 *
 * `empty` comes first: `set_line` refuses a line emptied to nothing (an unprojected
 * line would shift every later unit id), so the panel restores the value and says so
 * rather than sending a request that cannot succeed.
 */
export function lineCommitDecision(
  next: { text: string; marks: Mark[] },
  committed: { text: string; marks: Mark[] },
): LineCommit {
  if (next.text.trim() === "") return { send: false, reason: "empty" };
  if (next.text === committed.text && sameMarks(next.marks, committed.marks)) {
    return { send: false, reason: "unchanged" };
  }
  return { send: true };
}

/** One edit intent, in the terms `schemas_editing.IntentIn` takes. */
export type EditIntent = {
  hash: string;
  kind:
    | "set_arg"
    | "set_line"
    | "insert_bullet"
    | "insert_item"
    | "insert_section"
    | "delete"
    | "move_section";
  /** set_arg, set_line. */
  unit?: string;
  /** set_arg, set_line: the value the client believes that unit still holds. */
  expect?: string;
  /** set_arg. */
  value?: string;
  /** set_line. */
  text?: string;
  /** set_line. */
  marks?: Mark[];
  /** insert_bullet, insert_item, delete, move_section. */
  target?: string;
  /** move_section. */
  dir?: "up" | "down";
  /**
   * insert_bullet, insert_item, insert_section: the "(to fill in)" a new entry's values
   * are written with, and insert_section's heading. They are content, not Typst, and
   * `apply_intent` has no locale -- so they come from `messages/*.json` here. Left out,
   * the server writes its own CJK defaults, which is how an English-locale résumé ended
   * up with 「（待填）」 in a `url:` argument.
   */
  placeholder?: string;
  /** insert_section. */
  title?: string;
};

/**
 * The `set_line` to send for this unit, or null when there is nothing left to send.
 *
 * Built from the projection as it is when the commit's **turn** comes, never from the
 * one the person was looking at when they typed. `/edit` compiles before it writes and
 * re-checks the hash afterwards, so an intent still carrying the hash from before the
 * previous commit is always refused -- and what gets refused is whatever the person
 * typed second. Null is the other half of the same idea: a commit whose text the one
 * before it already carried has nothing to do, so it costs no request and no compile.
 */
export function lineIntent(
  projection: Projection,
  unitId: string,
  next: { text: string; marks: Mark[] },
): EditIntent | null {
  const unit = projection.units[unitId];
  if (!unit || unit.kind !== "line") return null;
  const view = unitView(unit);
  if (!view.editable) return null;
  // A unit is one line. `serialize_unit` refuses a newline ("a line unit is one line"),
  // which would reach the person as an English compiler message -- and pasting a bullet
  // copied out of a job description is among the likeliest things this form meets. Each
  // newline becomes a space instead, one character for one, so every mark offset still
  // points where it did. (`domToMarks` does the same for a `<br>` and a block element,
  // which is how a paste usually arrives; a literal newline survives `white-space:
  // pre-wrap`, so both paths need it.)
  const text = next.text.replace(/[\r\n]/g, " ");
  if (!lineCommitDecision({ text, marks: next.marks }, {
    text: unit.value,
    marks: view.marks,
  }).send) {
    return null;
  }
  return {
    hash: projection.hash,
    kind: "set_line",
    unit: unitId,
    expect: unit.value,
    text,
    marks: next.marks,
  };
}

/** The `set_arg` to send for this unit, or null when there is nothing left to send. */
export function argIntent(
  projection: Projection,
  unitId: string,
  value: string,
): EditIntent | null {
  const unit = projection.units[unitId];
  if (!unit || unit.kind !== "arg" || !unit.editable) return null;
  if (value === unit.value) return null;
  // `apply_intent` refuses a value with a newline in it ("a value is one line"); an
  // `<input>` cannot hold one, but a paste into a contenteditable row could.
  if (value.includes("\n") || value.includes("\r")) return null;
  return { hash: projection.hash, kind: "set_arg", unit: unitId, expect: unit.value, value };
}

export type EditRefusal = {
  code: string;
  detail: string;
  /**
   * The machine-readable half, where the code alone does not say which refusal it was.
   * `unsupported` covers four for `insert_item` -- `positional`, `no_emitter`,
   * `template_shape`, `nothing_to_copy` -- and only one of them is the sentence the
   * panel used to show for all of them. Empty when the server sent none.
   */
  reason: string;
};

/**
 * `/edit`'s refusal out of an `ApiError`'s body: `stale_hash`, `expect_mismatch`,
 * `compile_failed` or `unsupported`. `_edit_error` nests it inside FastAPI's `detail`,
 * where `errorMessage` in `api.ts` cannot see it (it only reads a string `detail`).
 */
export function editRefusalOf(body: unknown): EditRefusal | null {
  const rec = record(body);
  if (!rec) return null;
  const detail = record(rec.detail);
  if (!detail || typeof detail.code !== "string") return null;
  return {
    code: detail.code,
    detail: typeof detail.detail === "string" ? detail.detail : "",
    reason: typeof detail.reason === "string" ? detail.reason : "",
  };
}
