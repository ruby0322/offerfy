import assert from "node:assert/strict";
import test from "node:test";
import type { UsageRefusal } from "@/lib/api";
import { rebuildRefusal } from "./preferenceRefusal";

function refusal(over: Partial<UsageRefusal> = {}): UsageRefusal {
  return {
    limit: "burst",
    detail: "More than 3 profile rebuilds in an hour",
    tier: "registered",
    reset_at: "2026-11-23T16:00:00Z",
    used_credits: 4,
    limit_credits: 20,
    turns_used: 0,
    turn_limit: null,
    next_tier: null,
    ...over,
  };
}

test("an hourly refusal says try again, whatever the tier", () => {
  // The reason this function exists. The hourly cap used to be 0 below the
  // matching tier, so a `burst` there meant "this button cannot work for you"
  // and the copy sent the person off to turn on employer introductions. Every
  // tier that can reach the endpoint now has a real cap, so the same refusal
  // means what it says: too often, try again.
  assert.deepEqual(rebuildRefusal(refusal({ tier: "registered" })), {
    key: "rebuildTooOften",
  });
  assert.deepEqual(rebuildRefusal(refusal({ tier: "matching" })), {
    key: "rebuildTooOften",
  });
});

test("a spent weekly allowance says when it comes back", () => {
  for (const limit of ["weekly_turns", "weekly_credits"] as const) {
    assert.deepEqual(rebuildRefusal(refusal({ limit })), {
      key: "rebuildOutOfAllowance",
      resetAt: "2026-11-23T16:00:00Z",
    });
  }
});

test("a refusal nobody anticipated does not claim to know why", () => {
  // global_ceiling and guest_pool are not this button's business (a guest
  // cannot reach the endpoint at all), so they fall back to the vaguest true
  // thing rather than to an explanation that would be invented.
  assert.deepEqual(rebuildRefusal(refusal({ limit: "global_ceiling" })), {
    key: "rebuildTooOften",
  });
});
