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
