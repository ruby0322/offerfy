import assert from "node:assert/strict";
import test from "node:test";

import {
  MAP_REQUIREMENTS_TOOL,
  PROPOSE_ACTIONS_TOOL,
  applyAction,
  applyState,
  parseActionList,
  parseRequirementMap,
} from "./tailor-actions";

function mapPayload(items: unknown[], over: Record<string, unknown> = {}) {
  return { name: MAP_REQUIREMENTS_TOOL, arguments: {}, result: { ok: true, items, ...over } };
}

function actionPayload(items: unknown[]) {
  return { name: PROPOSE_ACTIONS_TOOL, arguments: {}, result: { ok: true, items } };
}

// --- the requirement table --------------------------------------------------

test("the table reads its rows and counts them by status", () => {
  const map = parseRequirementMap(
    mapPayload([
      { requirement: "三年以上後端經驗", status: "in_resume" },
      { requirement: "Kubernetes", status: "partly_in_resume" },
      { requirement: "Go", status: "not_in_resume" },
    ]),
  );
  assert.equal(map?.total, 3);
  assert.equal(map?.counts.in_resume, 1);
  assert.equal(map?.counts.unclear, 0);
});

test("a quote the server could not find is dropped, not shown with a caveat", () => {
  // The row's claim is about the posting and stands on its own; the quote is
  // the one part that would be a fabrication.
  const map = parseRequirementMap(
    mapPayload([
      {
        requirement: "Kubernetes",
        status: "partly_in_resume",
        resume_quote: "ran a Kubernetes fleet",
        resume_quote_found: false,
      },
    ]),
  );
  assert.equal(map?.items[0].resumeQuote, null);
});

test("a quote the server did find travels to the card", () => {
  const map = parseRequirementMap(
    mapPayload([
      {
        requirement: "英文書面溝通",
        status: "in_resume",
        resume_quote: "與美國團隊以英文協作",
        resume_quote_found: true,
      },
    ]),
  );
  assert.equal(map?.items[0].resumeQuote, "與美國團隊以英文協作");
});

test("a row with a status this build does not know is skipped", () => {
  const map = parseRequirementMap(
    mapPayload([
      { requirement: "a", status: "excellent" },
      { requirement: "b", status: "in_resume" },
    ]),
  );
  assert.equal(map?.total, 1);
  assert.equal(map?.items[0].requirement, "b");
});

test("the table reader refuses anything that is not its own tool", () => {
  assert.equal(parseRequirementMap(null), null);
  assert.equal(parseRequirementMap({ name: "read_job", arguments: {}, result: { ok: true } }), null);
  assert.equal(parseRequirementMap(mapPayload([])), null);
  // A failed call carries no ok flag, and rendering its error as a table
  // would be worse than rendering nothing.
  assert.equal(
    parseRequirementMap({
      name: MAP_REQUIREMENTS_TOOL,
      arguments: {},
      result: { error: "requirements must be a list" },
    }),
    null,
  );
});

// --- the suggestion list ----------------------------------------------------

test("the list reads its items and the server's verdict", () => {
  const list = parseActionList(
    actionPayload([
      { id: "a1", title: "改寫摘要", before: "x", after: "y", appliable: true, reason: null },
      { id: "a2", title: "補上 Go", before: "z", after: "w", appliable: false, reason: "before_missing" },
    ]),
  );
  assert.equal(list?.total, 2);
  assert.equal(list?.appliable, 1);
  assert.equal(list?.items[1].reason, "before_missing");
});

test("an item with no pair is an open suggestion, not a dropped one", () => {
  // The shape that lets the list answer a gap in the requirement table: a gap
  // is exactly the case with no `before` to copy.
  const list = parseActionList(
    actionPayload([
      {
        id: "a1",
        kind: "open",
        title: "技能區要不要補上 Go",
        why: "履歷沒有任何 Go 的痕跡",
        asks: ["我有用過 Go", "沒有 Go 經驗，跳過"],
        appliable: false,
        reason: "no_preview",
      },
    ]),
  );
  assert.equal(list?.total, 1);
  assert.equal(list?.items[0].kind, "open");
  assert.equal(list?.items[0].before, null);
  assert.deepEqual(list?.items[0].asks, ["我有用過 Go", "沒有 Go 經驗，跳過"]);
  // It is not appliable, so it must not count toward the apply tally.
  assert.equal(list?.appliable, 0);
});

test("a rewrite is read as an edit and carries no asks by default", () => {
  const list = parseActionList(
    actionPayload([
      { id: "a1", kind: "edit", title: "x", before: "a", after: "b", appliable: true },
    ]),
  );
  assert.equal(list?.items[0].kind, "edit");
  assert.deepEqual(list?.items[0].asks, []);
});

test("the kind is inferred from the pair when the server sent none", () => {
  // So a frontend ahead of the API still renders both shapes.
  const list = parseActionList(
    actionPayload([
      { id: "a1", title: "pair", before: "a", after: "b" },
      { id: "a2", title: "no pair" },
    ]),
  );
  assert.deepEqual(
    list?.items.map((i) => i.kind),
    ["edit", "open"],
  );
});

test("half a pair is never rendered as an edit", () => {
  // The server drops these, but an edit card with no anchor would show an
  // apply button that cannot work, so the reader refuses it too.
  const list = parseActionList(
    actionPayload([{ id: "a1", kind: "edit", title: "half", before: "a" }]),
  );
  assert.equal(list, null);
});

test("non-string asks are discarded rather than rendered", () => {
  const list = parseActionList(
    actionPayload([{ id: "a1", title: "open", asks: ["keep", 7, "", null] }]),
  );
  assert.deepEqual(list?.items[0].asks, ["keep"]);
});

// --- apply state: the part that must not be naive ---------------------------

const SOURCE = "= CV\n\n- Built the payments API\n- Shipped the dashboard\n";

test("a unique before is appliable", () => {
  assert.equal(applyState(SOURCE, "- Shipped the dashboard", "- Shipped two dashboards"), "appliable");
});

test("a before the document repeats is ambiguous", () => {
  const twice = "- Same line\n- Same line\n";
  assert.equal(applyState(twice, "- Same line", "- Changed line"), "ambiguous");
});

test("a before that is not there at all is missing", () => {
  assert.equal(applyState(SOURCE, "- Never written", "- Something"), "missing");
});

test("an identical pair is a no-op", () => {
  assert.equal(applyState(SOURCE, "= CV", "= CV"), "noop");
  assert.equal(applyState(SOURCE, "", "anything"), "noop");
});

test("an added clause reads as applied, not as appliable again", () => {
  // THE case this function exists for. `after` contains `before`, so the
  // naive "before is gone" rule reports not-applied and a second click
  // appends the clause twice.
  const before = "- Built the payments API";
  const after = "- Built the payments API serving 2M requests/day";
  const applied = applyAction(SOURCE, before, after);
  assert.ok(applied);
  assert.ok(applied.includes(after));
  assert.equal(applyState(applied, before, after), "applied");
});

test("a removed clause is not reported applied before it is used", () => {
  // The mirror case: `before` contains `after`, so the naive "after is
  // present" rule hides the button on a change nobody made yet.
  const source = "= CV\n\n- Built the API (intern project)\n";
  const before = "- Built the API (intern project)";
  const after = "- Built the API";
  assert.equal(applyState(source, before, after), "appliable");
  const applied = applyAction(source, before, after);
  assert.equal(applied, "= CV\n\n- Built the API\n");
  assert.equal(applyState(applied!, before, after), "applied");
});

test("applying twice is impossible, because the second call refuses", () => {
  const before = "- Built the payments API";
  const after = "- Built the payments API serving 2M requests/day";
  const once = applyAction(SOURCE, before, after)!;
  assert.equal(applyAction(once, before, after), null);
});

test("applying replaces exactly one occurrence and nothing else", () => {
  const applied = applyAction(SOURCE, "- Shipped the dashboard", "- Shipped two dashboards")!;
  assert.ok(applied.includes("- Built the payments API"));
  assert.ok(applied.includes("- Shipped two dashboards"));
  assert.ok(!applied.includes("- Shipped the dashboard\n"));
});

test("applying refuses every state that is not appliable", () => {
  assert.equal(applyAction("- Same\n- Same\n", "- Same", "- Other"), null);
  assert.equal(applyAction(SOURCE, "- Never written", "- Something"), null);
  assert.equal(applyAction(SOURCE, "= CV", "= CV"), null);
});

test("CJK spans apply as exactly as ASCII ones", () => {
  const source = "= 王小明\n\n- 以 Docker 打包服務\n";
  const applied = applyAction(source, "- 以 Docker 打包服務", "- 以 Docker 打包服務，並以 Kubernetes 管理")!;
  assert.equal(applied, "= 王小明\n\n- 以 Docker 打包服務，並以 Kubernetes 管理\n");
});

test("a pure deletion applies once and then reads as done", () => {
  const source = "= CV\n\n- Keep this\n- Drop this\n";
  assert.equal(applyState(source, "- Drop this\n", ""), "appliable");
  const applied = applyAction(source, "- Drop this\n", "")!;
  assert.equal(applied, "= CV\n\n- Keep this\n");
  assert.equal(applyState(applied, "- Drop this\n", ""), "applied");
});

test("a before living inside an already-applied after elsewhere is not the one replaced", () => {
  // Two lines start the same, and one of them already carries the suggested
  // wording. The free occurrence is the other one.
  const source = "- Built the API serving 2M/day\n- Built the API\n";
  const applied = applyAction(source, "- Built the API\n", "- Built the API serving 2M/day\n")!;
  assert.equal(applied, "- Built the API serving 2M/day\n- Built the API serving 2M/day\n");
});

test("an indented span keeps its indentation", () => {
  // The model is told to copy `before` including indentation, so a reader
  // that trimmed would turn a correct proposal into a missing one.
  const source = "= CV\n\n  - nested bullet\n";
  assert.equal(applyState(source, "  - nested bullet", "  - nested bullet, expanded"), "appliable");
});
