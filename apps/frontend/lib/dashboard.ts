import { apiJson } from "@/lib/api";
import type { Application } from "@/lib/applications";
import type { Digest } from "@/lib/digest";
import type { FactsState, JobPreference } from "@/lib/jobs";

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
  applications: Application[];
  resume_health: ResumeHealth;
  employer_views: EmployerViews;
  digest: Digest | null;
};

export async function fetchDashboard(): Promise<Dashboard> {
  return apiJson<Dashboard>("/api/v1/dashboard");
}
