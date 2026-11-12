import type { AppLocale } from "@/i18n/routing";
import { apiJson } from "@/lib/api";

// taiwanjobs is retired and no longer ingested, but historical rows keep the
// value, so it stays in the union and in sourceLabel.
export type JobSource =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "smartrecruiters"
  | "workday"
  | "taiwanjobs";

export type JobListItem = {
  id: string;
  source: JobSource;
  company: string;
  title: string;
  location: string | null;
  remote: boolean | null;
  apply_url: string;
  source_url: string;
  posted_at: string | null;
  last_seen_at: string;
  is_active: boolean;
};

export type JobDetail = JobListItem & {
  description_html: string;
  description_text: string;
  first_seen_at: string;
};

export type JobList = {
  items: JobListItem[];
  next_cursor: string | null;
  total: number;
};

export type JobFacetOption = {
  value: string;
  count: number;
};

export type JobFacetOptions = {
  countries: JobFacetOption[];
  regions: JobFacetOption[];
  seniorities: JobFacetOption[];
  functions: JobFacetOption[];
};

export type JobSitemapPage = {
  page: number;
  page_size: number;
  total_pages: number;
  items: { id: string; last_seen_at: string }[];
};

export type JobQuery = {
  q?: string;
  remote?: string;
  country?: string;
  region?: string;
  seniority?: string;
  function?: string;
  postedWithinDays?: string;
};

function backendBase(): string {
  return (process.env.BACKEND_INTERNAL_URL || "http://backend:8000").replace(/\/$/, "");
}

async function backendJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${backendBase()}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function hasJobFilters(query: JobQuery): boolean {
  return Boolean(
    query.q?.trim() ||
      query.remote === "true" ||
      query.remote === "false" ||
      query.country ||
      query.region ||
      query.seniority ||
      query.function ||
      query.postedWithinDays,
  );
}

export function jobListParams(query: JobQuery, extra?: { cursor?: string; limit?: number }): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.remote === "true" || query.remote === "false") {
    params.set("remote", query.remote);
  }
  if (query.country) params.set("country", query.country);
  if (query.region) params.set("region", query.region);
  if (query.seniority) params.set("seniority", query.seniority);
  if (query.function) params.set("function", query.function);
  if (query.postedWithinDays) params.set("posted_within_days", query.postedWithinDays);
  if (extra?.cursor) params.set("cursor", extra.cursor);
  params.set("limit", String(extra?.limit ?? 20));
  return params;
}

export function jobQueryString(query: JobQuery): string {
  const params = jobListParams(query);
  params.delete("limit");
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function fetchJobList(query: JobQuery): Promise<JobList> {
  const qs = jobListParams(query).toString();
  const data = await backendJson<JobList>(`/v1/jobs?${qs}`);
  return data ?? { items: [], next_cursor: null, total: 0 };
}

export async function fetchJobListPage(query: JobQuery, cursor: string): Promise<JobList> {
  const qs = jobListParams(query, { cursor }).toString();
  const res = await fetch(`/api/v1/jobs?${qs}`, { cache: "no-store" });
  if (!res.ok) return { items: [], next_cursor: null, total: 0 };
  return (await res.json()) as JobList;
}

export async function fetchFeaturedJobs(): Promise<JobListItem[]> {
  const data = await backendJson<JobList>("/v1/jobs/featured");
  return data?.items ?? [];
}

export async function fetchJobFacets(): Promise<JobFacetOptions | null> {
  return backendJson<JobFacetOptions>("/v1/jobs/facets");
}

export async function fetchJob(id: string): Promise<JobDetail | null> {
  return backendJson<JobDetail>(`/v1/jobs/${encodeURIComponent(id)}`);
}

export async function fetchJobSitemapPage(page: number): Promise<JobSitemapPage | null> {
  return backendJson<JobSitemapPage>(`/v1/jobs/sitemap?page=${page}`);
}

export function excerpt(text: string, max = 180): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  return `${compact.slice(0, max).trimEnd()}…`;
}

export function sourceLabel(source: string, locale: AppLocale): string {
  if (source === "taiwanjobs") {
    if (locale === "zh-CN") return "台湾就业通";
    if (locale === "zh-TW") return "台灣就業通";
    return "TaiwanJobs";
  }
  if (source === "greenhouse") return "Greenhouse";
  if (source === "lever") return "Lever";
  if (source === "ashby") return "Ashby";
  if (source === "smartrecruiters") return "SmartRecruiters";
  if (source === "workday") return "Workday";
  return source;
}

// --- Job preferences ---------------------------------------------------------

export type PreferenceActivity = "actively_looking" | "browsing" | "paused";
export type FactsState = "missing" | "stale" | "fresh";

/** Mirrors JobPreferenceOut. Lists on the wire; the pipe form is storage only. */
export type JobPreference = {
  target_functions: string[];
  target_locations: string[];
  target_seniority: string | null;
  /** true remote-only, false on-site only, null no preference. */
  remote_required: boolean | null;
  activity: PreferenceActivity;
  /** null before the first save: the seed is computed, never stored. */
  updated_at: string | null;
  facts_state: FactsState;
};

/** The body of a full replacement: anything left out is cleared server-side. */
export type JobPreferenceInput = Pick<
  JobPreference,
  "target_functions" | "target_locations" | "target_seniority" | "remote_required" | "activity"
>;

export type ResumeFactsRefresh = {
  facts_state: FactsState;
  error: string | null;
};

export async function fetchJobPreferences(): Promise<JobPreference> {
  const raw = await apiJson<Partial<JobPreference>>("/api/v1/me/job-preferences");
  // ?? null, not String(): an absent seniority must stay a real null.
  return {
    target_functions: raw.target_functions ?? [],
    target_locations: raw.target_locations ?? [],
    target_seniority: raw.target_seniority ?? null,
    remote_required: raw.remote_required ?? null,
    activity: raw.activity ?? "actively_looking",
    updated_at: raw.updated_at ?? null,
    facts_state: raw.facts_state ?? "missing",
  };
}

export async function saveJobPreferences(input: JobPreferenceInput): Promise<JobPreference> {
  // Built field by field so nothing but the five known keys can reach the
  // wire, and remote_required stays a boolean or null (StrictBool: "true" is a 422).
  const body: JobPreferenceInput = {
    target_functions: input.target_functions,
    target_locations: input.target_locations,
    target_seniority: input.target_seniority,
    remote_required: input.remote_required,
    activity: input.activity,
  };
  return apiJson<JobPreference>("/api/v1/me/job-preferences", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function refreshResumeFacts(): Promise<ResumeFactsRefresh> {
  const raw = await apiJson<Partial<ResumeFactsRefresh>>("/api/v1/me/resume-facts/refresh", {
    method: "POST",
  });
  return { facts_state: raw.facts_state ?? "missing", error: raw.error ?? null };
}

// The remote select's value is a string; the wire wants true / false / null
// (StrictBool: "true" or 1 is a 422), and false must never collapse to null.
export function remoteToValue(v: boolean | null): string {
  return v === null ? "" : v ? "true" : "false";
}

export function valueToRemote(v: string): boolean | null {
  return v === "true" ? true : v === "false" ? false : null;
}

// --- Match count -------------------------------------------------------------

export async function fetchMatchCount(): Promise<number> {
  const raw = await apiJson<{ count?: number }>("/api/v1/me/matches/count");
  return typeof raw.count === "number" ? raw.count : 0;
}

export type SummaryLabels = {
  seniority: (value: string) => string;
  fn: (value: string) => string;
  country: (value: string) => string;
  remote: string;
  onsite: string;
};

/**
 * One line describing what a person is looking for, e.g.
 * "senior · backend, infra · Taiwan". Empty when the preference says nothing,
 * so the caller can tell "no preference yet" from "no matches".
 */
export function preferenceSummary(
  pref: Pick<
    JobPreference,
    "target_functions" | "target_locations" | "target_seniority" | "remote_required"
  >,
  labels: SummaryLabels,
): string {
  const parts: string[] = [];
  if (pref.target_seniority) parts.push(labels.seniority(pref.target_seniority));
  if (pref.target_functions.length) parts.push(pref.target_functions.map(labels.fn).join(", "));
  if (pref.remote_required === true) parts.push(labels.remote);
  else if (pref.target_locations.length) {
    parts.push(pref.target_locations.map((c) => labels.country(c.toUpperCase())).join(", "));
  } else if (pref.remote_required === false) parts.push(labels.onsite);
  return parts.join(" · ");
}
