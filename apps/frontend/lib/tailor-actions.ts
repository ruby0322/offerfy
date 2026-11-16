/**
 * The requirement table and the suggestion list, as the transcript reads
 * them.
 *
 * Both arrive as ordinary tool traces (`app/services/tailor_tools.py`), so
 * they are already persisted, already excluded from what the model re-reads,
 * and already in `GET /messages` -- nothing here needed a column or a new
 * message role. This module is the reader, plus the one piece of real logic:
 * deciding whether a suggestion can still be applied to the document as it
 * stands right now.
 *
 * That question cannot be answered from the chat row. The row is immutable
 * JSON written when the model proposed the change, and the server's
 * `occurrences` describes the draft at that moment -- which may be several
 * edits ago. So the state is recomputed from the live source on every render,
 * and the server's flags are used only to explain *why* something was never
 * appliable in the first place.
 */

import type { ToolPayload } from "./chat-tools";

export const MAP_REQUIREMENTS_TOOL = "map_jd_requirements";
export const PROPOSE_ACTIONS_TOOL = "propose_resume_actions";

export const REQUIREMENT_STATUSES = [
  "in_resume",
  "partly_in_resume",
  "not_in_resume",
  "unclear",
] as const;

export type RequirementStatus = (typeof REQUIREMENT_STATUSES)[number];

export type TailorRequirement = {
  requirement: string;
  status: RequirementStatus;
  /** The phrase from the resume, kept only when the server found it there. */
  resumeQuote: string | null;
  note: string | null;
};

export type TailorRequirementMap = {
  items: TailorRequirement[];
  total: number;
  counts: Record<RequirementStatus, number>;
};

/**
 * Which shape a suggestion is.
 *
 * `edit` carries a before/after pair and gets an apply button. `open` carries
 * neither, because the resume had nothing to rewrite or what to write depends
 * on something only the person knows -- a gap in the requirement table is
 * exactly that case, so this shape is what lets the list answer every gap
 * instead of skipping the ones that mattered most.
 */
export type TailorActionKind = "edit" | "open";

export type TailorAction = {
  id: string;
  kind: TailorActionKind;
  title: string;
  where: string | null;
  requirement: string | null;
  why: string | null;
  /** Null on an `open` suggestion. */
  before: string | null;
  after: string | null;
  /**
   * Replies the person can send in one tap, in their own words.
   *
   * Optional, and empty on most `edit` items by design: a concrete rewrite
   * already has an apply button, so these only earn their place where there
   * is nothing to apply.
   */
  asks: string[];
  /** The server's verdict against the draft at proposal time. */
  appliable: boolean;
  reason: string | null;
};

export type TailorActionList = {
  items: TailorAction[];
  total: number;
  appliable: number;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function resultOf(payload: ToolPayload | null, name: string): Record<string, unknown> | null {
  if (!payload || payload.name !== name) return null;
  const result = asRecord(payload.result);
  if (!result || result.ok !== true) return null;
  return result;
}

/**
 * The requirement table, or null for anything that is not one.
 *
 * Null rather than a partial table on a shape this build does not recognise:
 * the alternative is rendering raw JSON at someone, which is the discipline
 * `parseTemplateSwitchPayload` already follows.
 */
export function parseRequirementMap(payload: ToolPayload | null): TailorRequirementMap | null {
  const result = resultOf(payload, MAP_REQUIREMENTS_TOOL);
  if (!result || !Array.isArray(result.items)) return null;

  const items: TailorRequirement[] = [];
  for (const raw of result.items) {
    const row = asRecord(raw);
    if (!row) continue;
    const requirement = asText(row.requirement);
    const status = row.status;
    if (!requirement || typeof status !== "string") continue;
    if (!(REQUIREMENT_STATUSES as readonly string[]).includes(status)) continue;
    items.push({
      requirement,
      status: status as RequirementStatus,
      // A quote the server could not find in the resume is dropped here
      // rather than shown with a caveat. The row's own claim is about the
      // posting and stands on its own; a quote we cannot stand behind is the
      // one part that would be a fabrication.
      resumeQuote: row.resume_quote_found === true ? asText(row.resume_quote) : null,
      note: asText(row.note),
    });
  }
  if (items.length === 0) return null;

  const counts = REQUIREMENT_STATUSES.reduce(
    (acc, status) => {
      acc[status] = items.filter((item) => item.status === status).length;
      return acc;
    },
    {} as Record<RequirementStatus, number>,
  );
  return { items, total: items.length, counts };
}

/** The suggestion list, or null for anything that is not one. */
export function parseActionList(payload: ToolPayload | null): TailorActionList | null {
  const result = resultOf(payload, PROPOSE_ACTIONS_TOOL);
  if (!result || !Array.isArray(result.items)) return null;

  const items: TailorAction[] = [];
  for (const raw of result.items) {
    const row = asRecord(raw);
    if (!row) continue;
    const title = asText(row.title);
    if (!title) continue;
    const before = asText(row.before);
    const after = asText(row.after);
    // Trust the server's `kind` where it sent one, and fall back to the pair
    // so a frontend ahead of the API still renders both shapes.
    const kind: TailorActionKind =
      row.kind === "open" || row.kind === "edit"
        ? row.kind
        : before !== null && after !== null
          ? "edit"
          : "open";
    // Half a pair is dropped server-side; this is the belt-and-braces for a
    // payload that predates that, since an edit card with no anchor would
    // render an apply button that cannot work.
    if (kind === "edit" && (before === null || after === null)) continue;
    items.push({
      id: asText(row.id) ?? `a${items.length + 1}`,
      kind,
      title,
      where: asText(row.where),
      requirement: asText(row.requirement),
      why: asText(row.why),
      before,
      after,
      asks: Array.isArray(row.asks)
        ? row.asks.filter((a): a is string => typeof a === "string" && a.length > 0)
        : [],
      appliable: row.appliable === true,
      reason: asText(row.reason),
    });
  }
  if (items.length === 0) return null;
  return {
    items,
    total: items.length,
    appliable: items.filter((item) => item.appliable).length,
  };
}

export type ApplyState = "appliable" | "applied" | "ambiguous" | "missing" | "noop";

function starts(haystack: string, needle: string): number[] {
  if (!needle) return [];
  const out: number[] = [];
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(needle, from);
    if (at === -1) return out;
    out.push(at);
    from = at + needle.length;
  }
}

/**
 * Where `before` still sits in `source` as something this change could
 * replace -- that is, every occurrence that is not already *inside* an
 * occurrence of `after`.
 *
 * That containment test is the whole trick, and it is what makes one rule
 * cover both shapes a suggestion takes:
 *
 * - **Adding** to a line puts `before` inside `after`
 *   (`"Built the API"` -> `"Built the API serving 2M/day"`). Once applied,
 *   the only `before` left in the document is the one inside the new text,
 *   so it is contained, so nothing is free: already applied. Without this
 *   test `before` still being present reads as not-applied, the button stays,
 *   and a second click appends the clause twice.
 * - **Removing** from a line puts `after` inside `before`. An `after`
 *   occurrence then cannot contain the longer `before`, so the containment
 *   test is simply false and every occurrence is free -- which is right,
 *   because an applied removal leaves no `before` at all. A rule that masked
 *   `after` out first would delete the very text it was trying to count, and
 *   report a removal as applied before anyone used it.
 */
function freeStarts(source: string, before: string, after: string): number[] {
  const afterStarts = starts(source, after);
  return starts(source, before).filter(
    (at) =>
      !afterStarts.some((from) => from <= at && at + before.length <= from + after.length),
  );
}

/**
 * Whether this suggestion can still be applied to `source` -- or already has
 * been.
 *
 * The obvious rule, "`before` is gone and `after` is there, so it was
 * applied", is wrong in the commonest case this feature produces. A
 * suggestion that *adds* a clause has `before` nested inside `after`:
 *
 *     before: "Built the payments API"
 *     after:  "Built the payments API serving 2M requests/day"
 *
 * After applying, `before` is still in the document, so that rule reports
 * not-applied, the button stays, and a second click appends the clause twice.
 * The mirror case -- a suggestion that removes text, so `after` is a
 * substring of `before` -- reports applied before it was ever used, and the
 * button disappears.
 *
 * `freeStarts` is what covers both: it asks whether any `before` survives
 * that is not already inside an `after`. Exact matching throughout, with no
 * whitespace normalisation -- both strings came from `typst_source` and are
 * compared against it, so normalising would add a class of mismatch rather
 * than remove one.
 */
export function applyState(source: string, before: string, after: string): ApplyState {
  if (!before || before === after) return "noop";
  const free = freeStarts(source, before, after);
  if (free.length === 1) return "appliable";
  if (free.length > 1) return "ambiguous";
  // Nothing left to replace. Either the change is in (an `after` is there,
  // which for an empty `after` means the text is simply gone), or the
  // document moved under the suggestion and it can no longer be placed.
  return source.includes(after) ? "applied" : "missing";
}

/**
 * The document with this suggestion applied, or null when it cannot be.
 *
 * Only ever replaces a single occurrence, and only when that occurrence is
 * unambiguous -- `applyState` is the gate, and this repeats the check rather
 * than trusting a caller to have run it, because the cost of being wrong is
 * someone's resume.
 */
export function applyAction(source: string, before: string, after: string): string | null {
  const free = freeStarts(source, before, after);
  // The gate is repeated rather than trusted to a caller, because the cost of
  // being wrong here is someone's resume.
  if (!before || before === after || free.length !== 1) return null;
  const at = free[0];
  return source.slice(0, at) + after + source.slice(at + before.length);
}
