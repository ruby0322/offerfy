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

export type JobEmployer = {
  company: string;
  /** Derived from the company name by the backend; see services/jobs/employers.py. */
  slug: string;
  open_count: number;
  new_count: number;
};

export type JobEmployerList = {
  items: JobEmployer[];
  total: number;
};

export type JobEmployerProfile = {
  company: string;
  slug: string;
  open_count: number;
  new_count: number;
  remote_count: number;
  /** When Offerfy first saw one of their postings, not when they were founded. */
  first_seen_at: string | null;
  last_seen_at: string | null;
  sources: JobSource[];
  cities: JobFacetOption[];
  countries: JobFacetOption[];
  functions: JobFacetOption[];
  seniorities: JobFacetOption[];
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
  /**
   * The three multi-select filters, each an OR: an empty list is "any".
   *
   * Lists rather than single values because the curated boards' chip rows
   * are multi-selects -- pick two employers, or Taipei and Hsinchu, and you
   * want either. They travel as repeated parameters (`?city=A&city=B`); see
   * services/jobs/search.py's param_values for why they are not comma-joined
   * the way `seniority` is.
   *
   * The ones that stayed single are the ones a set would not help: `country`
   * anchors which cities are on offer, `remote` is a yes/no, and
   * `postedWithinDays` is a window, not a member of a vocabulary.
   */
  company?: string[];
  city?: string[];
  function?: string[];
  country?: string;
  region?: string;
  seniority?: string;
  postedWithinDays?: string;
};

/** Whatever the URL said for a multi-select key, as the list JobQuery wants. */
export function valueList(raw: string | string[] | undefined): string[] {
  const all = Array.isArray(raw) ? raw : raw === undefined ? [] : [raw];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const name of all) {
    const value = name.trim();
    // Case-folded for the dedupe only: the stored spelling is what the chip
    // and the backend's exact match both want back.
    if (!value || seen.has(value.toLowerCase())) continue;
    seen.add(value.toLowerCase());
    out.push(value);
  }
  return out;
}

/** The list with `name` added, or removed if it was already there. */
export function toggleValue(current: string[] | undefined, name: string): string[] {
  const list = current ?? [];
  const folded = name.trim().toLowerCase();
  const without = list.filter((item) => item.trim().toLowerCase() !== folded);
  return without.length < list.length ? without : [...list, name];
}

export function hasValue(current: string[] | undefined, name: string): boolean {
  const folded = name.trim().toLowerCase();
  return (current ?? []).some((item) => item.trim().toLowerCase() === folded);
}

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

/** How long the landing page may quote a stale employer count, in seconds. */
const EMPLOYER_COUNT_TTL = 3600;

function backendBase(): string {
  return (process.env.BACKEND_INTERNAL_URL || "http://backend:8000").replace(/\/$/, "");
}

/**
 * Like backendJson, but cached for `seconds` instead of no-store.
 *
 * For a number on a page that is otherwise static: the landing wall wants the
 * live employer count, and fetching it per request would turn the most-visited
 * page on the site into a dynamic one for a figure that moves a few times a
 * day. An hour-old count beats a hardcoded one that nobody remembers to bump.
 */
async function backendJsonCached<T>(path: string, seconds: number): Promise<T | null> {
  try {
    const res = await fetch(`${backendBase()}${path}`, { next: { revalidate: seconds } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
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
      query.company?.length ||
      query.city?.length ||
      query.function?.length ||
      query.country ||
      query.region ||
      query.seniority ||
      query.postedWithinDays,
  );
}

/** How many filters are set, for "clear N filters". */
export function jobFilterCount(query: JobQuery): number {
  return JOB_QUERY_KEYS.filter((key) => {
    const value = query[key];
    if (key === "remote") return value === "true" || value === "false";
    // A multi-select facet counts once however many values it names: three
    // chips in one row are one filter to clear, not three.
    if (Array.isArray(value)) return value.length > 0;
    return Boolean(value && value.trim());
  }).length;
}

export function jobListParams(query: JobQuery, extra?: { cursor?: string; limit?: number }): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.remote === "true" || query.remote === "false") {
    params.set("remote", query.remote);
  }
  // Repeated, not joined: the backend reads these three as lists, and a
  // value with a comma in it must not become two employers or two cities.
  for (const name of valueList(query.company)) params.append("company", name);
  for (const name of valueList(query.city)) params.append("city", name);
  for (const name of valueList(query.function)) params.append("function", name);
  if (query.country) params.set("country", query.country);
  if (query.region) params.set("region", query.region);
  if (query.seniority) params.set("seniority", query.seniority);
  if (query.postedWithinDays) params.set("posted_within_days", query.postedWithinDays);
  if (extra?.cursor) params.set("cursor", extra.cursor);
  params.set("limit", String(extra?.limit ?? 20));
  return params;
}

/**
 * The same filters as a *page* query string, in the URL's own key names.
 *
 * `jobQueryString` writes the API's spelling (`posted_within_days`), which is
 * right for a fetch and wrong for an href: a link built from it comes back
 * through `visitorQueryFrom`, which reads `postedWithinDays`, and the filter
 * would be silently dropped on the way in. This is for links — one employer
 * chip's href carries every other filter the visitor had set.
 */
export function jobPageQuery(query: JobQuery): string {
  const params = new URLSearchParams();
  for (const key of JOB_QUERY_KEYS) {
    const value = query[key];
    if (Array.isArray(value)) {
      for (const name of valueList(value)) params.append(key, name);
    } else if (value && value.trim()) {
      params.set(key, value.trim());
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
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
 *
 * Cached for an hour. `boardCounts()` is the only caller, and it asks for all
 * six boards on every board page view using each board's own fixed query -- so
 * these six URLs are byte-identical across every request, visitor filters
 * included. Uncached they were six of the ~ten calls a single view checked out
 * *in parallel*, which is what exhausted the connection pool under crawler
 * load. The figure moves a few times a day; an hour old is fine.
 */
export async function fetchJobCount(query: JobQuery): Promise<number | undefined> {
  const qs = jobListParams(query, { limit: 1 }).toString();
  const data = await backendJsonCached<Partial<JobList>>(`/v1/jobs?${qs}`, 3600);
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

/**
 * Every employer with a live posting. Shape-checked for the reason
 * fetchJobPulse spells out: a 200 is not a promise about the body, and the
 * index page maps over `items` directly.
 */
export async function fetchJobEmployers(): Promise<JobEmployerList> {
  const raw = await backendJson<Partial<JobEmployerList>>("/v1/jobs/employers");
  if (!raw || !Array.isArray(raw.items)) return { items: [], total: 0 };
  return {
    items: raw.items,
    total: typeof raw.total === "number" ? raw.total : raw.items.length,
  };
}

/**
 * How many employers have something open, or null when the catalog cannot be
 * reached. Same number GET /v1/jobs/employers reports to the index page, so a
 * claim made next to a link to that page cannot disagree with it.
 */
export async function fetchJobEmployerCount(): Promise<number | null> {
  const raw = await backendJsonCached<Partial<JobEmployerList>>(
    "/v1/jobs/employers",
    EMPLOYER_COUNT_TTL,
  );
  return typeof raw?.total === "number" ? raw.total : null;
}

/** One employer, or null when no live employer claims that slug (a 404). */
export async function fetchJobEmployer(slug: string): Promise<JobEmployerProfile | null> {
  const raw = await backendJson<Partial<JobEmployerProfile>>(
    `/v1/jobs/employers/${encodeURIComponent(slug)}`,
  );
  if (!raw || typeof raw.company !== "string") return null;
  return {
    company: raw.company,
    slug: typeof raw.slug === "string" ? raw.slug : slug,
    open_count: typeof raw.open_count === "number" ? raw.open_count : 0,
    new_count: typeof raw.new_count === "number" ? raw.new_count : 0,
    remote_count: typeof raw.remote_count === "number" ? raw.remote_count : 0,
    first_seen_at: typeof raw.first_seen_at === "string" ? raw.first_seen_at : null,
    last_seen_at: typeof raw.last_seen_at === "string" ? raw.last_seen_at : null,
    sources: Array.isArray(raw.sources) ? raw.sources : [],
    cities: facetList(raw.cities),
    countries: facetList(raw.countries),
    functions: facetList(raw.functions),
    seniorities: facetList(raw.seniorities),
  };
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

// --- The daily ingest, by day ------------------------------------------------

/**
 * The calendar day a posting belongs to, as `YYYY-MM-DD`, or `"unknown"`.
 *
 * posted_at is absent for most of Workday, whose boards publish "Posted
 * Yesterday" as a phrase rather than a date; those fall back to last_seen_at,
 * which is when the catalogue last confirmed them. That fallback is the
 * reason this is a function and not an inline `.slice(0, 10)`: the dashboard
 * groups by it and the card deck labels each card with it, and the two must
 * never disagree about which day a posting arrived on.
 */
export function postingDay(job: Pick<JobListItem, "posted_at" | "last_seen_at">): string {
  const parsed = Date.parse(job.posted_at ?? job.last_seen_at);
  return Number.isNaN(parsed) ? "unknown" : new Date(parsed).toISOString().slice(0, 10);
}

export type PostingDay = { day: string; items: JobListItem[] };

/**
 * Postings arriving on the same calendar day, newest day first, each day in
 * the order given.
 *
 * `"unknown"` sorts ahead of every real day, because the sort is a plain
 * string comparison and `"u"` outranks any digit. That is the behaviour the
 * Matches tab has shipped with since the day-grouping landed, so it is kept
 * deliberately rather than quietly corrected: the list and the deck read this
 * one function, and a change here moves the undated group on both at once.
 */
export function groupByDay(items: JobListItem[]): PostingDay[] {
  const by = new Map<string, JobListItem[]>();
  for (const job of items) {
    const day = postingDay(job);
    by.set(day, [...(by.get(day) ?? []), job]);
  }
  return [...by.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([day, rows]) => ({ day, items: rows }));
}
