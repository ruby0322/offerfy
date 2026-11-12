import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  buildScript,
  frameAt,
  PHASES,
  SOURCE_LINE,
  sameFrame,
  scriptDuration,
  streamTokens,
  type MockFrame,
  type MockLengths,
} from "./mock-script";

function copy(locale: string): { prompt: string; reply: string } {
  const path = join(import.meta.dirname, "..", "..", "messages", `${locale}.json`);
  const messages = JSON.parse(readFileSync(path, "utf8"));
  const mock = messages.landing.mock;
  return { prompt: mock.chatPrompt, reply: mock.chatReply };
}

function lengths(locale: string): MockLengths {
  const { prompt, reply } = copy(locale);
  return {
    promptChars: prompt.length,
    replyTokens: streamTokens(reply).tokens.length,
    lineChars: SOURCE_LINE.length,
  };
}

const EN = lengths("en");

/** Every frame of one loop, sampled finely enough to catch a single phase. */
function sweep(len: MockLengths, stepMs = 10): MockFrame[] {
  const total = scriptDuration(buildScript(len));
  const frames: MockFrame[] = [];
  for (let t = 0; t < total; t += stepMs) {
    frames.push(frameAt(t, len));
  }
  return frames;
}

test("the script plays every phase exactly once, in order", () => {
  const script = buildScript(EN);
  assert.deepEqual(
    script.map((step) => step.phase),
    PHASES,
  );
  assert.equal(new Set(script.map((step) => step.phase)).size, script.length);
  for (const step of script) {
    assert.ok(step.ms > 0, `${step.phase} has no duration`);
  }
});

test("the poster shows the chat beside the finished PDF", () => {
  // This is the server-rendered frame, the first client paint, and the frame a
  // reduced-motion viewer keeps forever. The old mock pinned the Typst tab
  // instead, so those viewers never saw the chat -- the one claim the hero makes.
  const poster = frameAt(0, EN);
  assert.equal(poster.phase, "poster");
  assert.equal(poster.tab, "chat");
  assert.equal(poster.updated, true);
  assert.equal(poster.diffOpen, true);
  assert.equal(poster.promptChars, EN.promptChars);
  assert.equal(poster.replyTokens, EN.replyTokens);
  assert.equal(poster.composing, false);
});

test("the PDF never updates before the edited line is fully written", () => {
  for (const frame of sweep(EN)) {
    if (frame.phase === "poster" || !frame.updated) continue;
    assert.equal(
      frame.lineChars,
      EN.lineChars,
      `${frame.phase} updated the preview with ${frame.lineChars}/${EN.lineChars} characters written`,
    );
  }
});

test("the highlight outlives the preview swap", () => {
  // The bug this guards: the old mock dropped the highlight on the exact beat
  // the preview changed, cutting the only visual tie between the two panes.
  const swapping = sweep(EN).filter((frame) => frame.phase === "swap" || frame.phase === "hold");
  assert.ok(swapping.length > 0);
  for (const frame of swapping) {
    assert.equal(frame.sweep, true);
    assert.equal(frame.updated, true);
  }
});

test("nothing streams or lands out of turn", () => {
  for (const frame of sweep(EN)) {
    if (frame.replyTokens > 0 && frame.phase !== "poster") {
      assert.equal(frame.promptChars, EN.promptChars, "a reply streamed mid-prompt");
    }
    if (frame.diffBar && frame.phase !== "poster") {
      assert.equal(frame.replyTokens, EN.replyTokens, "a diff landed mid-reply");
    }
    if (frame.lineChars > 0 && frame.phase !== "poster") {
      assert.equal(frame.diffOpen, true, "the file changed before the diff was shown");
    }
  }
});

test("text only ever grows within a phase", () => {
  const frames = sweep(EN, 5);
  for (let i = 1; i < frames.length; i += 1) {
    const prev = frames[i - 1];
    const next = frames[i];
    if (prev.phase !== next.phase) continue;
    assert.ok(next.promptChars >= prev.promptChars, `prompt went backwards in ${next.phase}`);
    assert.ok(next.replyTokens >= prev.replyTokens, `reply went backwards in ${next.phase}`);
    assert.ok(next.lineChars >= prev.lineChars, `source went backwards in ${next.phase}`);
  }
});

test("each writing phase finishes its own text", () => {
  const frames = sweep(EN, 1);
  const last = (phase: string) => frames.filter((frame) => frame.phase === phase).at(-1);
  assert.equal(last("ask")?.promptChars, EN.promptChars);
  assert.equal(last("stream")?.replyTokens, EN.replyTokens);
  assert.equal(last("file")?.lineChars, EN.lineChars);
});

test("the loop is long enough to read and short enough to watch", () => {
  for (const locale of ["en", "zh-TW", "zh-CN"]) {
    const total = scriptDuration(buildScript(lengths(locale)));
    assert.ok(total > 8_000, `${locale} loop is only ${total}ms`);
    assert.ok(total < 16_000, `${locale} loop runs ${total}ms`);
  }
});

test("the finished state rests longer than any single step of getting there", () => {
  const script = buildScript(EN);
  const resting = script.filter((step) => step.phase === "poster" || step.phase === "hold");
  const working = script.filter((step) => step.phase !== "poster" && step.phase !== "hold");
  const longestRest = Math.max(...resting.map((step) => step.ms));
  const longestWork = Math.max(...working.map((step) => step.ms));
  assert.ok(longestRest >= longestWork, `rest ${longestRest}ms vs work ${longestWork}ms`);
});

test("replies stream by word in English and by character in Chinese", () => {
  const en = streamTokens(copy("en").reply);
  assert.equal(en.sep, " ");
  assert.ok(en.tokens.length > 4, "English reply did not split into words");
  assert.ok(en.tokens.every((token) => !token.includes(" ")));

  for (const locale of ["zh-TW", "zh-CN"]) {
    const zh = streamTokens(copy(locale).reply);
    assert.equal(zh.sep, "", `${locale} reply would stream in one tick`);
    assert.ok(zh.tokens.length > 8, `${locale} reply split into ${zh.tokens.length} tokens`);
  }
});

test("joining the streamed tokens reproduces the reply", () => {
  for (const locale of ["en", "zh-TW", "zh-CN"]) {
    const reply = copy(locale).reply;
    const { tokens, sep } = streamTokens(reply);
    assert.equal(tokens.join(sep), reply.trim().replace(/\s+/g, sep === " " ? " " : ""));
  }
});

test("sameFrame tells identical frames from neighbouring ones", () => {
  assert.equal(sameFrame(frameAt(0, EN), frameAt(1, EN)), true);
  const mid = Math.floor(scriptDuration(buildScript(EN)) / 2);
  assert.equal(sameFrame(frameAt(0, EN), frameAt(mid, EN)), false);
});

test("the loop wraps instead of running off the end", () => {
  const total = scriptDuration(buildScript(EN));
  assert.equal(sameFrame(frameAt(total, EN), frameAt(0, EN)), true);
  assert.equal(sameFrame(frameAt(total * 3 + 25, EN), frameAt(25, EN)), true);
});
