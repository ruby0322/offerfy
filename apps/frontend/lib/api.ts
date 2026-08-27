export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const rec = body as Record<string, unknown>;
    if (typeof rec.detail === "string") return rec.detail;
    if (typeof rec.message === "string") return rec.message;
    if (typeof rec.error === "string") return rec.error;
    if (Array.isArray(rec.detail) && rec.detail[0]) {
      const first = rec.detail[0] as Record<string, unknown>;
      if (typeof first.msg === "string") return first.msg;
    }
  }
  return fallback;
}

const COMPILE_FAIL_PREFIX = /^Typst compile failed:\s*/i;

export function typstCompileDetail(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status !== 400 && err.status !== 500 && err.status !== 504) return null;
  const raw = err.message.trim();
  if (!raw) return null;
  if (
    COMPILE_FAIL_PREFIX.test(raw) ||
    /timed out/i.test(raw) ||
    /produced no output/i.test(raw)
  ) {
    return raw.replace(COMPILE_FAIL_PREFIX, "").trim() || raw;
  }
  return raw;
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, {
    ...init,
    headers,
    credentials: "include",
  });
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init);
  const body = await parseBody(res);
  if (!res.ok) {
    throw new ApiError(errorMessage(body, res.statusText || "Request failed"), res.status, body);
  }
  return body as T;
}

export async function apiBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    const body = await parseBody(res);
    throw new ApiError(errorMessage(body, res.statusText || "Request failed"), res.status, body);
  }
  return res.blob();
}

export type ResumeSource = "create" | "upload";

export type ImportStatus = "idle" | "pending" | "done" | "failed";

export type Resume = {
  id: string;
  title?: string | null;
  typst_source: string;
  source?: ResumeSource;
  locale?: string;
  import_status?: ImportStatus;
  created_at?: string;
  updated_at?: string;
};

export type ChatMessage = {
  id?: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp?: string;
};

export type AtsCheck = {
  name: string;
  passed: boolean;
};

export type AtsReport = {
  checks: AtsCheck[];
};

export type AuthUser = {
  id?: string;
  email?: string | null;
  locale?: string | null;
};

function asResume(data: unknown): Resume {
  if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    if (rec.resume && typeof rec.resume === "object") {
      return asResume(rec.resume);
    }
    const id = rec.id ?? rec.resume_id;
    if (typeof id === "string") {
      return { ...(rec as Resume), id };
    }
  }
  return data as Resume;
}

function asResumeList(data: unknown): Resume[] {
  if (Array.isArray(data)) return data as Resume[];
  if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    if (Array.isArray(rec.resumes)) return rec.resumes as Resume[];
    if (Array.isArray(rec.items)) return rec.items as Resume[];
  }
  return [];
}

function asMessages(data: unknown): ChatMessage[] {
  if (Array.isArray(data)) return data as ChatMessage[];
  if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    if (Array.isArray(rec.messages)) return rec.messages as ChatMessage[];
  }
  return [];
}

export async function createResume(payload: {
  title?: string;
  locale?: string;
}): Promise<Resume> {
  const data = await apiJson<unknown>("/api/v1/resumes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return asResume(data);
}

export async function uploadResume(payload: {
  file: File;
  title?: string;
  locale?: string;
}): Promise<Resume> {
  const form = new FormData();
  form.append("file", payload.file);
  if (payload.title) form.append("title", payload.title);
  if (payload.locale) form.append("locale", payload.locale);
  const data = await apiJson<unknown>("/api/v1/resumes/upload", {
    method: "POST",
    body: form,
  });
  return asResume(data);
}

export async function listResumes(): Promise<Resume[]> {
  const data = await apiJson<unknown>("/api/v1/resumes");
  return asResumeList(data);
}

export async function getResume(id: string): Promise<Resume> {
  return asResume(await apiJson<unknown>(`/api/v1/resumes/${id}`));
}

export async function putResumeSource(
  id: string,
  payload: { typst_source?: string; title?: string },
): Promise<Resume> {
  return asResume(
    await apiJson<unknown>(`/api/v1/resumes/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  );
}

export async function getPreviewPages(id: string): Promise<string[]> {
  const data = await apiJson<unknown>(`/api/v1/resumes/${id}/preview`);
  if (data && typeof data === "object" && Array.isArray((data as { pages?: unknown }).pages)) {
    return (data as { pages: unknown[] }).pages.filter((page): page is string => typeof page === "string");
  }
  return [];
}

export async function compileResume(
  id: string,
  format: "svg" | "pdf",
): Promise<Blob> {
  return apiBlob(`/api/v1/resumes/${id}/compile`, {
    method: "POST",
    body: JSON.stringify({ format }),
  });
}

export async function getAtsReport(id: string): Promise<AtsReport> {
  const data = await apiJson<unknown>(`/api/v1/resumes/${id}/ats`);
  if (data && typeof data === "object" && Array.isArray((data as AtsReport).checks)) {
    return data as AtsReport;
  }
  if (Array.isArray(data)) {
    return { checks: data as AtsCheck[] };
  }
  return { checks: [] };
}

export async function exportPdf(id: string): Promise<Blob> {
  return apiBlob(`/api/v1/resumes/${id}/export`);
}

export type UniverseTemplate = {
  name: string;
  version: string;
  description: string;
  universe_url: string;
  import_line: string;
  apply_prompt: string;
  cached: boolean;
};

export function templatePreviewUrl(name: string): string {
  return `/api/v1/templates/${encodeURIComponent(name)}/preview`;
}

export async function listTemplates(): Promise<UniverseTemplate[]> {
  const data = await apiJson<unknown>("/api/v1/templates");
  if (data && typeof data === "object" && Array.isArray((data as { templates?: unknown }).templates)) {
    return (data as { templates: UniverseTemplate[] }).templates.filter(
      (row) => row && typeof row.name === "string" && typeof row.apply_prompt === "string",
    );
  }
  return [];
}

export type ChatStreamEvent =
  | { type: "tool"; message: ChatMessage }
  | { type: "source"; typst_source: string; applied: boolean }
  | { type: "assistant"; message: ChatMessage }
  | { type: "done"; typst_source: string; applied: boolean }
  | { type: "error"; detail: string; status: number };

export type ChatSendOptions = {
  preferFullSource?: boolean;
};

function parseSseBlock(block: string): ChatStreamEvent | null {
  const line = block.trim();
  if (!line.startsWith("data:")) return null;
  try {
    const parsed = JSON.parse(line.slice(5).trim()) as ChatStreamEvent;
    if (parsed && typeof parsed === "object" && typeof parsed.type === "string") {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

export async function sendChat(
  id: string,
  message: string,
  file: File | null | undefined,
  options: ChatSendOptions | undefined,
  onEvent: (event: ChatStreamEvent) => void | Promise<void>,
): Promise<void> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "text/event-stream" },
  };
  if (file) {
    const form = new FormData();
    form.append("message", message);
    form.append("file", file, file.name);
    if (options?.preferFullSource) {
      form.append("prefer_full_source", "true");
    }
    init.body = form;
  } else {
    init.body = JSON.stringify({
      message,
      ...(options?.preferFullSource ? { prefer_full_source: true } : {}),
    });
  }
  const res = await apiFetch(`/api/v1/resumes/${id}/chat`, init);
  if (!res.ok) {
    const body = await parseBody(res);
    throw new ApiError(errorMessage(body, res.statusText || "Request failed"), res.status, body);
  }
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("event-stream") || !res.body) {
    throw new ApiError("Chat response was not a stream", res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const block of parts) {
      const event = parseSseBlock(block);
      if (event) await onEvent(event);
    }
  }
  buffer += decoder.decode();
  const tail = parseSseBlock(buffer);
  if (tail) await onEvent(tail);
}

export async function getChatMessages(id: string): Promise<ChatMessage[]> {
  try {
    const data = await apiJson<unknown>(`/api/v1/resumes/${id}/messages`);
    return asMessages(data);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      return [];
    }
    throw err;
  }
}

export async function getMe(): Promise<AuthUser | null> {
  try {
    const data = await apiJson<unknown>("/api/v1/auth/me");
    if (!data) return null;
    if (typeof data === "object") {
      const rec = data as Record<string, unknown>;
      if (rec.user === null) return null;
      const user = (rec.user ?? rec) as AuthUser;
      if (user && (user.email || user.id)) return user;
    }
    return null;
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 404)) {
      return null;
    }
    throw err;
  }
}

export async function claimResumes(): Promise<void> {
  try {
    await apiJson("/api/v1/auth/claim", { method: "POST" });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      return;
    }
    throw err;
  }
}

export async function logout(): Promise<void> {
  await apiJson("/api/v1/auth/logout", { method: "POST" });
}

export function googleStartUrl(): string {
  return "/api/v1/auth/google/start";
}
