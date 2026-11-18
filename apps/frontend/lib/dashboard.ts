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

export type MatchArrival = {
  job_id: string;
  job_title: string;
  company: string;
  apply_url: string;
  /** False when the posting closed after it was scored; the row says so. */
  job_is_active: boolean;
  location: string | null;
  /** Null with the score: not looked at yet, never a poor match. */
  tier: string | null;
  fit_score: number | null;
  reason: string | null;
  gap: string | null;
  scored_at: string | null;
};

export type Dashboard = {
  preference: JobPreference;
  match_count: number;
  /**
   * Scored for this person since they last looked, measured by the server,
   * plus matching postings the sweep has not reached yet (no score).
   */
  arrivals: MatchArrival[];
  /** True exactly when the server has never recorded a look by this person. */
  first_visit: boolean;
  /** The first few matching postings, newest first — the card shows these. */
  matches: JobListItem[];
  applications: Application[];
  resume_health: ResumeHealth;
  employer_views: EmployerViews;
  digest: Digest | null;
};

export async function fetchDashboard(): Promise<Dashboard> {
  return apiJson<Dashboard>("/api/v1/dashboard");
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
