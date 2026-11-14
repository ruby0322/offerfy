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
  /** The biggest employers only; employer_total is how many there are. */
  companies: JobFacetOption[];
  employer_total: number;
};

export type JobCityOptions = {
  country: string;
  items: JobFacetOption[];
};

export type JobPulseEmployer = {
  company: string;
  open_count: number;
  new_count: number;
};

export type JobPulse = {
  active_total: number;
  new_24h: number;
  employer_count: number;
  /** When ingest last confirmed anything, not when this page rendered. */
  refreshed_at: string | null;
  newest: JobListItem[];
  top_employers: JobPulseEmployer[];
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
  company?: string;
  country?: string;
  region?: string;
  city?: string;
  seniority?: string;
  function?: string;
  postedWithinDays?: string;
};

/** The query keys a facet owns, in the order the filter bar shows them. */
export const JOB_QUERY_KEYS = [
  "q",
  "company",
  "country",
  "city",
  "region",
  "remote",
  "seniority",
  "function",
  "postedWithinDays",
] as const satisfies readonly (keyof JobQuery)[];

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
      query.company ||
      query.country ||
      query.region ||
      query.city ||
      query.seniority ||
      query.function ||
      query.postedWithinDays,
  );
}

/** How many filters are set, for "clear N filters". */
export function jobFilterCount(query: JobQuery): number {
  return JOB_QUERY_KEYS.filter((key) => {
    const value = query[key];
    if (key === "remote") return value === "true" || value === "false";
    return Boolean(value && value.trim());
  }).length;
}

export function jobListParams(query: JobQuery, extra?: { cursor?: string; limit?: number }): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.remote === "true" || query.remote === "false") {
    params.set("remote", query.remote);
  }
  if (query.company) params.set("company", query.company);
  if (query.city) params.set("city", query.city);
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

const EMPTY_LIST: JobList = { items: [], next_cursor: null, total: 0 };

/** Same reasoning as fetchJobPulse: a 200 is not a promise about the body. */
function asJobList(raw: Partial<JobList> | null): JobList {
  if (!raw || !Array.isArray(raw.items)) return EMPTY_LIST;
  return {
    items: raw.items,
    next_cursor: typeof raw.next_cursor === "string" ? raw.next_cursor : null,
    total: typeof raw.total === "number" ? raw.total : raw.items.length,
  };
}

export async function fetchJobList(query: JobQuery): Promise<JobList> {
  const qs = jobListParams(query).toString();
  return asJobList(await backendJson<Partial<JobList>>(`/v1/jobs?${qs}`));
}

export async function fetchJobListPage(query: JobQuery, cursor: string): Promise<JobList> {
  const qs = jobListParams(query, { cursor }).toString();
  const res = await fetch(`/api/v1/jobs?${qs}`, { cache: "no-store" });
  if (!res.ok) return EMPTY_LIST;
  return asJobList((await res.json()) as Partial<JobList>);
}

// NOTE: /v1/jobs/featured (and the spotlight scoring behind it) no longer
// has a caller. The live board replaced the "worth a look" strip it fed with
// "just posted" and "hiring hardest", both of which are facts rather than a
// ranking. The endpoint, its config and its tests are untouched — whether to
// retire them or give the spotlight a home again is a product call, not a
// side effect of this redesign.

/** Shape-checked, for the reason fetchJobPulse spells out. */
function facetList(value: unknown): JobFacetOption[] {
  return Array.isArray(value) ? (value as JobFacetOption[]) : [];
}

export async function fetchJobFacets(query: JobQuery = {}): Promise<JobFacetOptions | null> {
  const params = jobListParams(query);
  params.delete("limit");
  const qs = params.toString();
  const raw = await backendJson<Partial<JobFacetOptions>>(
    `/v1/jobs/facets${qs ? `?${qs}` : ""}`,
  );
  if (!raw) return null;
  return {
    countries: facetList(raw.countries),
    regions: facetList(raw.regions),
    seniorities: facetList(raw.seniorities),
    functions: facetList(raw.functions),
    companies: facetList(raw.companies),
    employer_total: typeof raw.employer_total === "number" ? raw.employer_total : 0,
  };
}

/**
 * The live board's data, or null when there is nothing trustworthy to show.
 *
 * Shape-checked rather than cast. `backendJson` only promises that the
 * response was 200 and parsed as JSON, and JobPulseBoard reads
 * `pulse.newest.length` directly — so a 200 carrying anything but a pulse
 * (a proxy's own JSON, a future field rename, a route that starts matching
 * a different handler) took the whole /jobs page down with a 500 rather
 * than hiding one section. Found by a stub whose path matching was too
 * loose, which is the same accident an API change would be.
 */
export async function fetchJobPulse(query: JobQuery = {}): Promise<JobPulse | null> {
  const params = jobListParams(query);
  params.delete("limit");
  const qs = params.toString();
  const raw = await backendJson<Partial<JobPulse>>(`/v1/jobs/pulse${qs ? `?${qs}` : ""}`);
  if (!raw || typeof raw.active_total !== "number") return null;
  return {
    active_total: raw.active_total,
    new_24h: typeof raw.new_24h === "number" ? raw.new_24h : 0,
    employer_count: typeof raw.employer_count === "number" ? raw.employer_count : 0,
    refreshed_at: typeof raw.refreshed_at === "string" ? raw.refreshed_at : null,
    newest: Array.isArray(raw.newest) ? raw.newest : [],
    top_employers: Array.isArray(raw.top_employers) ? raw.top_employers : [],
  };
}

/**
 * How many roles a query matches, and nothing else. Used for the curated
 * board counts, where six numbers are wanted and none of the postings are.
 */
export async function fetchJobCount(query: JobQuery): Promise<number | undefined> {
  const qs = jobListParams(query, { limit: 1 }).toString();
  const data = await backendJson<Partial<JobList>>(`/v1/jobs?${qs}`);
  return typeof data?.total === "number" ? data.total : undefined;
}

/** Cities for one country, from the server (board pages render these). */
export async function fetchJobCitiesServer(country: string): Promise<JobFacetOption[]> {
  const data = await backendJson<JobCityOptions>(
    `/v1/jobs/cities?country=${encodeURIComponent(country)}`,
  );
  return data?.items ?? [];
}

/**
 * Cities for one country, fetched from the browser when the picker opens.
 * Not preloaded for all 18 countries: that is 18 queries to render one menu
 * whose contents nobody may ever look at.
 */
export async function fetchJobCities(country: string): Promise<JobFacetOption[]> {
  const res = await fetch(`/api/v1/jobs/cities?country=${encodeURIComponent(country)}`, {
    cache: "no-store",
  });
  if (!res.ok) return [];
  const data = (await res.json()) as Partial<JobCityOptions>;
  return Array.isArray(data.items) ? data.items : [];
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
