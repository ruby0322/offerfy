/**
 * The published tier table, fetched server-side for the /usage page.
 *
 * Deliberately not in `messages/*.json`. A published allowance that has
 * drifted from the enforced one is the worst bug this page could have, and
 * reading it from the API makes that drift impossible rather than merely
 * tested for. It is also the cheaper thing to operate on this host: tuning
 * 4 / 20 / 100 is then a code change plus a zero-downtime `kill -HUP` of the
 * API, where a number baked into the message files needs a frontend rebuild
 * and ~40s of downtime (AGENTS.md section 4).
 *
 * Fetched on the server rather than in the browser so the numbers are in the
 * HTML: this page is indexable, and a crawler must not see an empty table.
 */

export type UsageTierRow = {
  name: "guest" | "registered" | "matching";
  credits: number | null;
  turns: number | null;
  approx_edits: number;
  can_publish: boolean;
};

export type UsageTiers = {
  tiers: UsageTierRow[];
  reset_at: string;
  enforced: boolean;
};

function backendBase(): string {
  return (process.env.BACKEND_INTERNAL_URL || "http://backend:8000").replace(/\/$/, "");
}

export async function fetchUsageTiers(): Promise<UsageTiers | null> {
  try {
    const res = await fetch(`${backendBase()}/v1/usage/tiers`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as UsageTiers;
  } catch {
    return null;
  }
}

/**
 * The model catalogue, with what each model costs relative to the default.
 *
 * Declared here rather than in lib/api.ts for the same reason UsageTiers is:
 * /usage and /pricing are indexable, so they fetch it on the *server* and a
 * crawler sees the numbers in the HTML. lib/api.ts re-exports these types for
 * the browser-side callers (the editor's picker and /plan) so there is one
 * shape rather than two that can drift.
 *
 * No per-token price. Nobody knows how many tokens their resume is, so a rate
 * is not a number anyone can act on; `cost_weight` and `approx_edits` are the
 * same information in the unit the product actually bills in.
 */
export type ModelRow = {
  id: string;
  /** The product name. Not translated -- these are proper nouns. */
  label: string;
  provider: "openai" | "anthropic";
  /** The generation, for grouping: 'gpt-6.1', 'claude-5.5'. */
  family: string;
  /** What one AI edit costs relative to the default model, which is 1.0. */
  cost_weight: number;
  /**
   * Edits a week's allowance buys on this model, keyed by tier. The guest
   * tier is absent: it is capped on a count of turns, so its figure does not
   * vary by model -- which is why guests only get the cheaper models.
   */
  approx_edits: Record<string, number>;
  /** The cheapest tier allowed to pick this. */
  min_tier: "guest" | "registered" | "matching";
  /** Whether the caller may pick it. Locked rows are sent, not hidden. */
  available: boolean;
  /** Prices seeded from a neighbouring generation, not yet confirmed. */
  prices_provisional: boolean;
};

export type ModelTable = {
  models: ModelRow[];
  /** What this caller's next turn runs on. */
  selected: string;
  default: string;
  /** The model every cost_weight is relative to. */
  reference: string;
  tier: "guest" | "registered" | "matching";
};

export async function fetchModelTable(): Promise<ModelTable | null> {
  try {
    const res = await fetch(`${backendBase()}/v1/models`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as ModelTable;
  } catch {
    return null;
  }
}
