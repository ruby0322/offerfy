import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { WALL_ITEMS, WALL_NAMES, wallRows } from "./companies";
import { EMPLOYER_COUNT } from "@/lib/seo";

// The board files live in the backend app, which the frontend image does not
// copy, so this guard runs on a full checkout only:
//   ./node_modules/.bin/tsx --test components/landing/companies.test.ts
const DATA = join(import.meta.dirname, "..", "..", "..", "backend", "data");

function boardCompanies(file: string): string[] {
  const raw = JSON.parse(readFileSync(join(DATA, file), "utf8")) as {
    boards: { company: string }[];
  };
  return raw.boards.map((board) => board.company);
}

function carried(): Set<string> {
  return new Set([
    ...boardCompanies("ats_boards.json"),
    ...boardCompanies("workday_boards.json"),
  ]);
}

test("every employer on the wall is a board we actually ingest", () => {
  const live = carried();
  const missing = WALL_NAMES.filter((name) => !live.has(name));
  assert.deepEqual(missing, [], `not in the board list: ${missing.join(", ")}`);
});

test("the wall has no duplicates", () => {
  assert.equal(new Set(WALL_NAMES).size, WALL_NAMES.length);
});

test("EMPLOYER_COUNT is the number of employers we actually carry", () => {
  // Employers, not boards: Wise holds two, so the board rows outnumber them.
  assert.equal(
    EMPLOYER_COUNT,
    carried().size,
    "the catalog changed — update EMPLOYER_COUNT in lib/seo.ts",
  );
});

test("a glyph carries a path and a wordmark does not", () => {
  for (const item of WALL_ITEMS) {
    if (item.d === null) {
      assert.equal(item.hex, null, `${item.name} has no path but carries a colour`);
    } else {
      assert.ok(item.d.length > 0, `${item.name} has an empty path`);
    }
  }
});

test("no row of the carousel is all wordmarks", () => {
  // Round-robin dealing used to alias against the wordmark spacing and hand
  // one row nothing but type. Contiguous slices must not regress to that.
  for (const row of wallRows(3)) {
    assert.ok(
      row.some((item) => item.d !== null),
      "a row came out with no marks in it",
    );
    assert.ok(row.length > 0);
  }
});

test("each row is long enough to fill a wide viewport", () => {
  for (const row of wallRows(3)) {
    assert.ok(row.length >= 8, `a row of ${row.length} leaves a gap on desktop`);
  }
});
