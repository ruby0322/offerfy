import { apiJson } from "@/lib/api";
import type { Application } from "@/lib/applications";
import type { Digest } from "@/lib/digest";
import type { FactsState, JobListItem, JobPreference } from "@/lib/jobs";

export type ResumeHealth = {
  has_master: boolean;
  published: boolean;
  facts_state: FactsState;
  matching_revision_current: boolean;
  needs_rebuild: boolean;
};

export type EmployerViews = {
  disclosure_count: number;
  last_disclosed_at: string | null;
};

export type Dashboard = {
  preference: JobPreference;
  match_count: number;
  /**
   * How many of those the catalogue first saw since the `since` we asked
   * with. Null when we did not ask -- a first visit has no window, and 0
   * would claim "nothing new" where the truth is "we never asked".
   */
  new_match_count: number | null;
  /** The first few matching postings, newest first — the card shows these. */
  matches: JobListItem[];
  applications: Application[];
  resume_health: ResumeHealth;
  employer_views: EmployerViews;
  digest: Digest | null;
};

export async function fetchDashboard(since?: string | null): Promise<Dashboard> {
  const query = since ? `?since=${encodeURIComponent(since)}` : "";
  return apiJson<Dashboard>(`/api/v1/dashboard${query}`);
}

export type SpendSlice = { purpose: string; credits: number; rounds: number };

export type UsageBreakdown = {
  period_start: string;
  slices: SpendSlice[];
  /** [Taipei calendar day, credits], in order. */
  daily: [string, number][];
  total_credits: number;
  unmeasured_rounds: number;
};

export async function fetchUsageBreakdown(): Promise<UsageBreakdown> {
  return apiJson<UsageBreakdown>("/api/v1/usage/breakdown");
}

export type DisclosureRecipient = {
  recipient_label: string | null;
  recipient_contact: string | null;
  first_disclosed_at: string;
  last_disclosed_at: string;
  disclosure_count: number;
};

export async function fetchDisclosures(): Promise<DisclosureRecipient[]> {
  return apiJson<DisclosureRecipient[]>("/api/v1/me/disclosures");
}
