import type { AdminDayPoint } from "./api";

/** The four token series the admin stack plots, in stacking order. */
export type TokenStack = {
  cached_input: number;
  fresh_input: number;
  answer_output: number;
  reasoning_output: number;
};

/**
 * Split one day's token totals into four series that do not overlap.
 *
 * The backend reports `cached_tokens` as a subset of `input_tokens` and
 * `reasoning_tokens` as a subset of `output_tokens`, so a stack of the raw
 * fields would count both subsets twice and inflate every bar. These four sum
 * to `input + output` instead.
 *
 * Both differences are clamped at zero. The only thing that produces a
 * negative here is a provider reporting more cached tokens than input, or more
 * reasoning than output -- `OPENAI_BASE_URL` may be a proxy, so that is not
 * ours to trust, and a negative would render as an inverted bar.
 */
export function toTokenStack(row: AdminDayPoint): TokenStack {
  return {
    cached_input: row.llm_cached_tokens,
    fresh_input: Math.max(0, row.llm_input_tokens - row.llm_cached_tokens),
    answer_output: Math.max(0, row.llm_output_tokens - row.llm_reasoning_tokens),
    reasoning_output: row.llm_reasoning_tokens,
  };
}

/**
 * One day's cache hit rate as a percentage, or `null` if nothing measured it.
 *
 * `null` is load-bearing and must not become `0`: with `connectNulls` off it
 * leaves a gap in the line, which is how "no call reported both halves" stays
 * distinguishable from "the cache missed". A real measured `0` is a miss and
 * does render on the axis.
 */
export function toCacheRate(row: AdminDayPoint): number | null {
  if (row.llm_cache_hit_rate === null) return null;
  return Math.round(row.llm_cache_hit_rate * 1000) / 10;
}

/** Tokens billed across the window; the token card's emptiness test. */
export function totalTokens(series: AdminDayPoint[]): number {
  return series.reduce(
    (sum, row) => sum + row.llm_input_tokens + row.llm_output_tokens,
    0,
  );
}

/**
 * How many days reported a hit rate -- the hit-rate card's emptiness test.
 *
 * Deliberately not the call count: vision calls bill input only, and a proxy
 * may drop `input_tokens_details` entirely, so a window can hold plenty of
 * calls and still have no rate to plot.
 */
export function measuredRateDays(series: AdminDayPoint[]): number {
  return series.filter((row) => row.llm_cache_hit_rate !== null).length;
}
