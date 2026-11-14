import { ApiError, apiJson } from "@/lib/api";

export type ApplicationStatus =
  | "saved"
  | "tailored"
  | "applied"
  | "interviewing"
  | "closed";

export type Application = {
  job_id: string;
  job_title: string;
  company: string;
  apply_url: string;
  /** A posting that has gone inactive still shows in the pipeline. */
  job_is_active: boolean;
  status: ApplicationStatus;
  source: string;
  tailor_resume_id: string | null;
  applied_at: string | null;
  applied_confirmed_at: string | null;
  /**
   * When the employer answered, whatever the answer was; null when none is
   * recorded. On a closed row null means nobody answered *or* that the row
   * predates the column, so a reply rate counts only what it can account for.
   * The backend stamps it once and never clears it.
   */
  replied_at: string | null;
  last_status_at: string;
  notes: string | null;
};

export type ApplicationPatch = {
  status?: "interviewing" | "closed";
  /**
   * Whether the employer ever answered. Only carries information alongside
   * `status: "closed"` — "not moving forward" covers both a rejection and
   * silence — and the API refuses it without a status rather than accepting
   * it and dropping it.
   */
  replied?: boolean;
  notes?: string;
};

// The same four values in the same order as the backend's GROUP_ORDER, which
// is authoritative: the groups render hottest first. `closed` is deliberately
// absent there; groupApplications gives it, and any status it does not know,
// a trailing group.
export const GROUP_ORDER: readonly ApplicationStatus[] = [
  "interviewing",
  "applied",
  "tailored",
  "saved",
];

export type ApplicationGroup = {
  status: string;
  items: Application[];
};

/**
 * Group rows by status in GROUP_ORDER, skipping empty groups. Order inside a
 * group is the order received (the API sends most recent first). A status
 * outside GROUP_ORDER (closed, or one a newer backend adds) is emitted as a
 * trailing group in order of first appearance, never dropped.
 */
export function groupApplications(items: Application[]): ApplicationGroup[] {
  const known = new Map<string, Application[]>(GROUP_ORDER.map((s) => [s, []]));
  const trailing = new Map<string, Application[]>();
  for (const item of items) {
    const bucket = known.get(item.status) ?? trailing.get(item.status);
    if (bucket) {
      bucket.push(item);
    } else {
      trailing.set(item.status, [item]);
    }
  }
  return [...known, ...trailing]
    .filter(([, rows]) => rows.length > 0)
    .map(([status, rows]) => ({ status, items: rows }));
}

const base = (jobId: string) => `/api/v1/me/applications/${encodeURIComponent(jobId)}`;

/** The person's row for this posting, or null when they have none. */
export async function fetchApplication(jobId: string): Promise<Application | null> {
  try {
    return await apiJson<Application>(base(jobId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function saveJob(jobId: string): Promise<Application> {
  return apiJson<Application>("/api/v1/me/applications", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
}

/** 204 on success; a row past `saved` is a 409 ApiError, and no row a 404. */
export async function unsaveJob(jobId: string): Promise<void> {
  await apiJson<unknown>(base(jobId), { method: "DELETE" });
}

export async function markApplied(jobId: string): Promise<Application> {
  return apiJson<Application>(`${base(jobId)}/applied`, { method: "POST" });
}

export async function confirmApplied(jobId: string, applied: boolean): Promise<Application> {
  return apiJson<Application>(`${base(jobId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ applied }),
  });
}

export async function patchApplication(
  jobId: string,
  input: ApplicationPatch,
): Promise<Application> {
  // Field by field so only the known keys reach the wire, and an omitted one
  // stays omitted rather than becoming null.
  const body: ApplicationPatch = {};
  if (input.status !== undefined) body.status = input.status;
  if (input.replied !== undefined) body.replied = input.replied;
  if (input.notes !== undefined) body.notes = input.notes;
  return apiJson<Application>(base(jobId), {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
