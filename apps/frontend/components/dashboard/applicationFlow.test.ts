import assert from "node:assert/strict";
import test from "node:test";
import type { Application } from "@/lib/applications";
import {
  buildApplicationFlow,
  employersOf,
  flowGeometry,
  LEAF_TONE,
  leafOf,
  type FlowLeafId,
} from "./applicationFlow";

function row(over: Partial<Application> = {}): Application {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    status: "saved",
    source: "search",
    tailor_resume_id: null,
    applied_at: null,
    applied_confirmed_at: null,
    replied_at: null,
    last_status_at: "2026-11-10T12:00:00Z",
    notes: null,
    ...over,
  };
}

const SENT = "2026-11-01T12:00:00Z";
const ANSWERED = "2026-11-05T12:00:00Z";

test("every row lands in exactly one leaf, and the leaves account for all of them", () => {
  // The invariant the diagram depends on: band widths are shares of the
  // total, so a row in two buckets or in none makes the picture a lie.
  const rows = [
    row({ status: "saved" }),
    row({ status: "tailored", tailor_resume_id: "t1" }),
    row({ status: "applied", applied_at: SENT, tailor_resume_id: "t1" }),
    row({ status: "interviewing", applied_at: SENT, replied_at: ANSWERED }),
    row({ status: "closed", applied_at: SENT, replied_at: ANSWERED }),
    row({ status: "closed", applied_at: SENT }),
    row({ status: "closed" }),
  ];
  const flow = buildApplicationFlow(rows);
  const total = Object.values(flow.leaves).reduce((a, b) => a + b, 0);
  assert.equal(total, rows.length);
  assert.equal(flow.tracked, rows.length);
  assert.deepEqual(flow.leaves, {
    onlySaved: 1,
    neverSent: 1,
    waiting: 1,
    // The interviewing row has replied_at set, but it has not come to rest
    // as a closed application: it belongs to the leaf it is still in.
    interviewing: 1,
    repliedClosed: 1,
    noAnswer: 1,
    droppedEarly: 1,
  });
});

test("a closed row splits on the stamp, not on the status", () => {
  const answered = row({ status: "closed", applied_at: SENT, replied_at: ANSWERED });
  const silent = row({ status: "closed", applied_at: SENT });
  const abandoned = row({ status: "closed" });
  assert.equal(leafOf(answered), "repliedClosed");
  assert.equal(leafOf(silent), "noAnswer");
  // Closed without ever sending is not a rejection; nobody was ever asked.
  assert.equal(leafOf(abandoned), "droppedEarly");
});

test("applying without tailoring is its own path into the sent node", () => {
  // The outbound click on a job page sends a row with no tailored resume. A
  // diagram drawing tailored -> sent as the only route would lose it.
  const rows = [
    row({ status: "applied", applied_at: SENT, tailor_resume_id: "t1" }),
    row({ status: "applied", applied_at: SENT }),
  ];
  const flow = buildApplicationFlow(rows);
  assert.equal(flow.sent, 2);
  assert.equal(flow.sentWithTailor, 1);
  assert.equal(flow.sentWithoutTailor, 1);
  assert.equal(flow.tailored, 1);
  assert.equal(
    flow.sentWithTailor + flow.sentWithoutTailor,
    flow.sent,
    "the two paths into sent must account for every sent row",
  );
});

test("tailored counts the link, whether or not the row was sent", () => {
  const rows = [
    row({ status: "tailored", tailor_resume_id: "t1" }),
    row({ status: "closed", tailor_resume_id: "t2", applied_at: SENT }),
    row({ status: "saved" }),
  ];
  assert.equal(buildApplicationFlow(rows).tailored, 2);
});

test("an unknown status is placed, never dropped", () => {
  // A newer backend may grow the vocabulary; the bands still have to sum.
  const sent = row({ status: "offer" as Application["status"], applied_at: SENT });
  const answered = row({
    status: "offer" as Application["status"],
    applied_at: SENT,
    replied_at: ANSWERED,
  });
  const untouched = row({ status: "shortlisted" as Application["status"] });
  assert.equal(leafOf(sent), "waiting");
  assert.equal(leafOf(answered), "repliedClosed");
  assert.equal(leafOf(untouched), "onlySaved");
  const flow = buildApplicationFlow([sent, answered, untouched]);
  assert.equal(
    Object.values(flow.leaves).reduce((a, b) => a + b, 0),
    3,
  );
});

test("an empty pipeline is all zeroes, not a hole", () => {
  const flow = buildApplicationFlow([]);
  assert.equal(flow.tracked, 0);
  assert.equal(
    Object.values(flow.leaves).reduce((a, b) => a + b, 0),
    0,
  );
});

test("the amber tone marks only the stalls the person can clear", () => {
  const stalled = (Object.keys(LEAF_TONE) as FlowLeafId[]).filter(
    (id) => LEAF_TONE[id] === "stalled",
  );
  assert.deepEqual(stalled.sort(), ["neverSent", "waiting"]);
});

test("employersOf names a leaf's companies and reports the overflow", () => {
  const rows = [
    row({ status: "closed", applied_at: SENT, company: "Appier" }),
    row({ status: "closed", applied_at: SENT, company: "Dcard" }),
    row({ status: "closed", applied_at: SENT, company: "91APP" }),
    // A different leaf, so it must not appear.
    row({ status: "applied", applied_at: SENT, company: "Gogolook" }),
  ];
  assert.deepEqual(employersOf(rows, "noAnswer", 2), {
    companies: ["Appier", "Dcard"],
    extra: 1,
  });
  assert.deepEqual(employersOf(rows, "noAnswer", 9), {
    companies: ["Appier", "Dcard", "91APP"],
    extra: 0,
  });
  assert.deepEqual(employersOf(rows, "waiting", 3), {
    companies: ["Gogolook"],
    extra: 0,
  });
  assert.deepEqual(employersOf(rows, "interviewing", 3), { companies: [], extra: 0 });
  // A zero cap reports everything as overflow rather than dividing by it.
  assert.deepEqual(employersOf(rows, "noAnswer", 0), { companies: [], extra: 3 });
});

// --- geometry -------------------------------------------------------------

const GEO = {
  width: 880,
  scale: 7,
  nodeWidth: 10,
  columns: [92, 300, 470, 630] as [number, number, number, number],
  gap: 12,
  padY: 12,
  minHeight: 6,
  labelBlock: 34,
  markSize: 15,
  markPitch: 18,
  labelInset: 8,
  overflowRoom: 26,
};

function geomOf(rows: Application[]) {
  return flowGeometry(buildApplicationFlow(rows), GEO);
}

test("node heights are proportional, and every band stays inside the box", () => {
  const rows = [
    ...Array.from({ length: 6 }, () => row({ status: "saved" })),
    ...Array.from({ length: 2 }, () =>
      row({ status: "tailored", tailor_resume_id: "t" }),
    ),
    ...Array.from({ length: 5 }, () => row({ status: "applied", applied_at: SENT })),
    row({ status: "interviewing", applied_at: SENT, replied_at: ANSWERED }),
    row({ status: "closed", applied_at: SENT, replied_at: ANSWERED }),
    ...Array.from({ length: 9 }, () => row({ status: "closed", applied_at: SENT })),
  ];
  const g = geomOf(rows);
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  assert.equal(byId.get("tracked")!.value, 24);
  assert.equal(byId.get("worked")!.value, 18, "24 tracked less 6 only saved");
  assert.equal(byId.get("sent")!.value, 16, "18 worked less the 2 never sent");
  // Proportional: 18 of 24 applications is three quarters of the height.
  assert.equal(byId.get("worked")!.h / byId.get("tracked")!.h, 18 / 24);
  for (const n of g.nodes) {
    assert.ok(n.y >= 0 && n.y + n.h <= g.height, `${n.id} escapes the viewBox`);
  }
});

test("the spine nodes account for every application", () => {
  const rows = [
    row({ status: "saved" }),
    row({ status: "tailored", tailor_resume_id: "t" }),
    row({ status: "closed" }),
    row({ status: "applied", applied_at: SENT }),
    row({ status: "interviewing", applied_at: SENT, replied_at: ANSWERED }),
  ];
  const g = geomOf(rows);
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const leafSum = ["onlySaved", "neverSent", "droppedEarly", "waiting", "interviewing"]
    .map((id) => byId.get(id as FlowLeafId)?.value ?? 0)
    .reduce((a, b) => a + b, 0);
  assert.equal(leafSum, rows.length);
  assert.equal(byId.get("tracked")!.value, rows.length);
  assert.equal(
    byId.get("worked")!.value + (byId.get("onlySaved")?.value ?? 0),
    rows.length,
  );
});

test("a path nothing took is drawn as nothing, not as a sliver", () => {
  // Everyone saved and nobody tailored: there is no worked node to draw, so
  // there must be no band pretending otherwise either.
  const g = geomOf([row({ status: "saved" }), row({ status: "saved" })]);
  const ids = g.nodes.map((n) => n.id);
  assert.deepEqual(ids.sort(), ["onlySaved", "tracked"]);
  assert.equal(g.bands.length, 1);
  assert.deepEqual(
    g.bands.map((b) => [b.from, b.to]),
    [["tracked", "onlySaved"]],
  );
});

test("one application beside ninety is still visible", () => {
  const rows = [
    row({ status: "interviewing", applied_at: SENT, replied_at: ANSWERED }),
    ...Array.from({ length: 90 }, () => row({ status: "closed", applied_at: SENT })),
  ];
  const g = geomOf(rows);
  const lone = g.nodes.find((n) => n.id === "interviewing")!;
  assert.ok(lone.h >= GEO.minHeight, "a floored node must not be sub-pixel");
});

test("an empty pipeline produces a box and no marks", () => {
  const g = geomOf([]);
  assert.deepEqual(g.nodes, []);
  assert.deepEqual(g.bands, []);
  assert.ok(g.height > 0, "still needs a box to render an empty state into");
});

test("bands leave their source in the order the destinations stack", () => {
  // Non-crossing is the whole reason the cursor walks the source node.
  const rows = [
    row({ status: "applied", applied_at: SENT }),
    row({ status: "tailored", tailor_resume_id: "t" }),
    row({ status: "saved" }),
  ];
  const g = geomOf(rows);
  const fromTracked = g.bands.filter((b) => b.from === "tracked");
  const dest = new Map(g.nodes.map((n) => [n.id, n.y]));
  for (let i = 1; i < fromTracked.length; i += 1) {
    assert.ok(
      dest.get(fromTracked[i].to)! > dest.get(fromTracked[i - 1].to)!,
      "destinations must descend in the order the bands are emitted",
    );
  }
});

test("no leaf label can collide with the one below it", () => {
  // The collision the band arithmetic cannot see: two one-application leaves
  // are only minHeight + gap apart, while a label plus its row of employer
  // marks is nearly three times that.
  const rows = [
    row({ status: "interviewing", applied_at: SENT, replied_at: ANSWERED }),
    row({ status: "closed", applied_at: SENT, replied_at: ANSWERED }),
    row({ status: "closed", applied_at: SENT }),
    row({ status: "applied", applied_at: SENT }),
  ];
  const g = geomOf(rows);
  const leaves = g.nodes
    .filter((n) => n.label)
    .sort((a, b) => a.x - b.x || a.y - b.y);
  for (let i = 1; i < leaves.length; i += 1) {
    const above = leaves[i - 1];
    const below = leaves[i];
    if (above.x !== below.x) continue;
    const bottomOfAbove = above.label!.markY + GEO.markSize;
    const topOfBelow = below.label!.y - 8;
    assert.ok(
      topOfBelow >= bottomOfAbove,
      `${above.id} (ends ${bottomOfAbove}) overlaps ${below.id} (starts ${topOfBelow})`,
    );
  }
});

test("labels and mark rows stay inside the box", () => {
  const rows = [
    ...Array.from({ length: 9 }, () => row({ status: "closed", applied_at: SENT })),
    row({ status: "applied", applied_at: SENT }),
    row({ status: "saved" }),
    row({ status: "tailored", tailor_resume_id: "t" }),
  ];
  const g = geomOf(rows);
  for (const node of g.nodes) {
    if (!node.label) continue;
    assert.ok(
      node.label.markY + GEO.markSize <= g.height,
      `${node.id} marks run past the bottom`,
    );
    const marksEnd =
      node.label.x + node.label.markCapacity * GEO.markPitch + GEO.overflowRoom;
    assert.ok(marksEnd <= g.width, `${node.id} marks run past the right edge`);
  }
});

test("mark capacity follows the room beside each leaf", () => {
  const rows = [
    row({ status: "saved" }),
    row({ status: "tailored", tailor_resume_id: "t" }),
    row({ status: "applied", applied_at: SENT }),
  ];
  const g = geomOf(rows);
  const cap = (id: FlowLeafId) =>
    g.nodes.find((n) => n.id === id)!.label!.markCapacity;
  // The last column runs to the right edge, so it holds the most; the
  // mid-flow stalls are boxed in by the next column and hold fewer.
  assert.ok(cap("waiting") > cap("onlySaved"), "the outcome column has more room");
  assert.ok(cap("onlySaved") > 0, "a mid-flow leaf still fits some marks");
  assert.ok(cap("neverSent") > 0);
});
