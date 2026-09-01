import type { AppLocale } from "@/i18n/routing";

export type JobSource = "greenhouse" | "lever" | "ashby" | "taiwanjobs";

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
};

export type JobSitemapPage = {
  page: number;
  page_size: number;
  total_pages: number;
  items: { id: string; last_seen_at: string }[];
};

export type JobQuery = {
  q?: string;
  source?: string;
  remote?: string;
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
  return Boolean(query.q?.trim() || query.source || query.remote === "true" || query.remote === "false");
}

export function jobListParams(query: JobQuery, extra?: { cursor?: string; limit?: number }): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.source) params.set("source", query.source);
  if (query.remote === "true" || query.remote === "false") {
    params.set("remote", query.remote);
  }
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
  return data ?? { items: [], next_cursor: null };
}

export async function fetchJobListPage(query: JobQuery, cursor: string): Promise<JobList> {
  const qs = jobListParams(query, { cursor }).toString();
  const res = await fetch(`/api/v1/jobs?${qs}`, { cache: "no-store" });
  if (!res.ok) return { items: [], next_cursor: null };
  return (await res.json()) as JobList;
}

export async function fetchFeaturedJobs(): Promise<JobListItem[]> {
  const data = await backendJson<JobList>("/v1/jobs/featured");
  return data?.items ?? [];
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
  return source;
}
