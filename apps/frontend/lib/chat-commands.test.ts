import assert from "node:assert/strict";
import test from "node:test";

import {
  commandTerm,
  filterCommands,
  moveCommandIndex,
  type ChatCommand,
} from "./chat-commands";

const COMMANDS: ChatCommand[] = [
  { key: "ats", label: "Fix the ATS checks", description: "Run the scan", prompt: "a" },
  { key: "bullets", label: "Make my bullets stronger", description: "Impact first", prompt: "b" },
  { key: "critique", label: "Review it like a recruiter", description: "Six seconds", prompt: "c" },
  { key: "stats", label: "Add numbers", description: "Quantify the bullets", prompt: "d" },
];

test("a lone slash opens the menu with everything", () => {
  assert.equal(commandTerm("/"), "");
  assert.deepEqual(filterCommands(COMMANDS, "").length, COMMANDS.length);
});

test("the term is whatever follows the slash, lowercased", () => {
  assert.equal(commandTerm("/ats"), "ats");
  assert.equal(commandTerm("/ATS"), "ats");
  assert.equal(commandTerm("/bull-ets_2"), "bull-ets_2");
});

test("prose with a slash in it does not open the menu", () => {
  for (const draft of [
    "and/or",
    "see https://offerfy.cc",
    "/ats and also fix the dates",
    "/ats\n",
    " /ats",
    "2024/06",
    "",
    "fix the ats checks",
  ]) {
    assert.equal(commandTerm(draft), null, draft);
  }
});

test("a slug prefix outranks a slug substring, which outranks the label", () => {
  // "ats" is a prefix of /ats, sits inside /stats, and appears in no label.
  assert.deepEqual(
    filterCommands(COMMANDS, "ats").map((command) => command.key),
    ["ats", "stats"],
  );
});

test("the label and description are searchable, so the slugs need not be known", () => {
  assert.deepEqual(
    filterCommands(COMMANDS, "recruiter").map((command) => command.key),
    ["critique"],
  );
  assert.deepEqual(
    filterCommands(COMMANDS, "quantify").map((command) => command.key),
    ["stats"],
  );
});

test("a single letter narrows to slug prefixes, or it would narrow to nothing", () => {
  // "a" sits in three of the four slugs and in most of the prose; only /ats
  // starts with it.
  assert.deepEqual(
    filterCommands(COMMANDS, "a").map((command) => command.key),
    ["ats"],
  );
});

test("no match is an empty list, not everything", () => {
  assert.deepEqual(filterCommands(COMMANDS, "zzz"), []);
});

test("filtering keeps the caller's order and does not mutate the list", () => {
  const before = COMMANDS.map((command) => command.key);
  filterCommands(COMMANDS, "");
  assert.deepEqual(
    COMMANDS.map((command) => command.key),
    before,
  );
});

test("the selection wraps at both ends", () => {
  assert.equal(moveCommandIndex(0, 3, -1), 2);
  assert.equal(moveCommandIndex(2, 3, 1), 0);
  assert.equal(moveCommandIndex(1, 3, 1), 2);
  // An index left over from a longer list must not escape the new one.
  assert.equal(moveCommandIndex(0, 0, 1), 0);
});
