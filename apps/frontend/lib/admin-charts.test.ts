import assert from "node:assert/strict";
import test from "node:test";
import type { AdminDayPoint } from "./api";
import {
  measuredRateDays,
  toCacheRate,
  toTokenStack,
  totalTokens,
} from "./admin-charts";

function day(overrides: Partial<AdminDayPoint> = {}): AdminDayPoint {
  return {
    date: "2026-10-23",
    users: 0,
    resumes_create: 0,
    resumes_upload: 0,
    chats: 0,
    guest_rate_chat: 0,
    guest_rate_export: 0,
    llm_calls: 0,
    llm_input_tokens: 0,
    llm_cached_tokens: 0,
    llm_output_tokens: 0,
    llm_reasoning_tokens: 0,
    llm_cache_hit_rate: null,
    llm_cost_uusd: 0,
    llm_cache_write_tokens: 0,
    llm_unmeasured_calls: 0,
    ...overrides,
  };
}

test("the four token series are disjoint and sum to input plus output", () => {
  const row = day({
    llm_input_tokens: 10_000,
    llm_cached_tokens: 4_000,
    llm_output_tokens: 1_200,
    llm_reasoning_tokens: 500,
  });
  const stack = toTokenStack(row);
  assert.deepEqual(stack, {
    cached_input: 4_000,
    fresh_input: 6_000,
    answer_output: 700,
    reasoning_output: 500,
  });
  const total =
    stack.cached_input + stack.fresh_input + stack.answer_output + stack.reasoning_output;
  // The guard against the naive stack: adding the raw fields would give
  // 15,700 by counting the cached and reasoning tokens twice.
  assert.equal(total, row.llm_input_tokens + row.llm_output_tokens);
  assert.equal(total, 11_200);
});

test("a day that reported no details puts every input token in the fresh bucket", () => {
  const stack = toTokenStack(day({ llm_input_tokens: 4_000, llm_output_tokens: 120 }));
  assert.equal(stack.cached_input, 0);
  assert.equal(stack.fresh_input, 4_000);
  assert.equal(stack.answer_output, 120);
  assert.equal(stack.reasoning_output, 0);
});

test("more cached tokens than input cannot invert a bar", () => {
  const stack = toTokenStack(day({ llm_input_tokens: 100, llm_cached_tokens: 900 }));
  assert.equal(stack.fresh_input, 0);
  assert.equal(stack.cached_input, 900);
});

test("more reasoning tokens than output cannot invert a bar", () => {
  const stack = toTokenStack(day({ llm_output_tokens: 100, llm_reasoning_tokens: 900 }));
  assert.equal(stack.answer_output, 0);
  assert.equal(stack.reasoning_output, 900);
});

test("an unmeasured rate stays null and a measured zero stays zero", () => {
  // The distinction the whole metric rests on: null is a gap in the line,
  // zero is a day the cache genuinely missed.
  assert.equal(toCacheRate(day({ llm_cache_hit_rate: null })), null);
  assert.equal(toCacheRate(day({ llm_cache_hit_rate: 0 })), 0);
});

test("a rate becomes a percentage rounded to one decimal", () => {
  assert.equal(toCacheRate(day({ llm_cache_hit_rate: 0.25 })), 25);
  assert.equal(toCacheRate(day({ llm_cache_hit_rate: 1 })), 100);
  assert.equal(toCacheRate(day({ llm_cache_hit_rate: 0.3456 })), 34.6);
});

test("calls without a rate leave the hit-rate card empty but the token card full", () => {
  // Vision-only traffic: the decisions endpoint bills input tokens only and
  // never reports a cached count, so there are calls and tokens but no rate.
  const series = [
    day({ llm_calls: 3, llm_input_tokens: 7_500, llm_cache_hit_rate: null }),
    day({ llm_calls: 2, llm_input_tokens: 2_500, llm_cache_hit_rate: null }),
  ];
  assert.equal(measuredRateDays(series), 0);
  assert.equal(totalTokens(series), 10_000);
});

test("only the days that measured a rate are counted", () => {
  const series = [
    day({ llm_cache_hit_rate: null }),
    day({ llm_cache_hit_rate: 0 }),
    day({ llm_cache_hit_rate: 0.8 }),
  ];
  // The measured zero counts: it is data, not a missing day.
  assert.equal(measuredRateDays(series), 2);
});

test("totals add input and output across the window", () => {
  const series = [
    day({ llm_input_tokens: 1_000, llm_output_tokens: 100 }),
    day({ llm_input_tokens: 2_000, llm_output_tokens: 200 }),
  ];
  assert.equal(totalTokens(series), 3_300);
  assert.equal(totalTokens([]), 0);
});
