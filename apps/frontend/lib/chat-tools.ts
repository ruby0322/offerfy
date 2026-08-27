import type { ChatMessage } from "@/lib/api";

export type ToolPayload = {
  name: string;
  arguments: Record<string, unknown>;
  result: unknown;
};

export function parseToolPayload(content: string): ToolPayload | null {
  try {
    const parsed = JSON.parse(content) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const rec = parsed as Record<string, unknown>;
    if (typeof rec.name !== "string") return null;
    const args =
      rec.arguments && typeof rec.arguments === "object" && !Array.isArray(rec.arguments)
        ? (rec.arguments as Record<string, unknown>)
        : {};
    return { name: rec.name, arguments: args, result: rec.result };
  } catch {
    return null;
  }
}

function resultError(result: unknown): string | null {
  if (result && typeof result === "object" && "error" in result) {
    const error = (result as { error: unknown }).error;
    if (typeof error === "string" && error.trim()) return error.trim();
    if (error) return String(error);
  }
  return null;
}

export function isSuccessfulTypstEdit(payload: ToolPayload | null): boolean {
  if (!payload || payload.name !== "apply_typst_edit") return false;
  if (resultError(payload.result)) return false;
  if (payload.result && typeof payload.result === "object") {
    const rec = payload.result as { ok?: unknown; changed?: unknown };
    if ("changed" in rec) return Boolean(rec.changed);
    if ("ok" in rec) return Boolean(rec.ok);
  }
  return true;
}

export function editPreviousSource(payload: ToolPayload | null): string | null {
  if (!isSuccessfulTypstEdit(payload) || !payload) return null;
  const result = payload.result;
  if (!result || typeof result !== "object") return null;
  const previous = (result as { previous_source?: unknown }).previous_source;
  return typeof previous === "string" && previous.length > 0 ? previous : null;
}

export function editResultSource(payload: ToolPayload | null): string | null {
  if (!isSuccessfulTypstEdit(payload) || !payload) return null;
  const result = payload.result;
  if (!result || typeof result !== "object") return null;
  const source = (result as { source?: unknown }).source;
  return typeof source === "string" && source.length > 0 ? source : null;
}

export function editCheckpointAction(
  payload: ToolPayload | null,
  currentSource: string,
): { kind: "restore" | "reapply"; source: string } | null {
  const previous = editPreviousSource(payload);
  if (!previous) return null;
  if (currentSource === previous) {
    const next = editResultSource(payload);
    if (!next || next === currentSource) return null;
    return { kind: "reapply", source: next };
  }
  return { kind: "restore", source: previous };
}

export function chatAppliedTypstEdit(
  messages: ChatMessage[],
  nextSource: string | null,
  previousSource: string,
  appliedFlag: boolean,
): boolean {
  if (appliedFlag) return true;
  if (nextSource && nextSource !== previousSource) return true;
  return messages.some(
    (message) => message.role === "tool" && isSuccessfulTypstEdit(parseToolPayload(message.content)),
  );
}

export function formatUserAttachmentMessage(message: string, filename: string | null): string {
  const text = message.trim();
  if (!filename) return text;
  return `Attached file: ${filename}\n\n${text || "Please use this file."}`;
}

export function parseAttachedUserMessage(content: string): { filename: string | null; text: string } {
  const match = content.match(/^Attached file: (.+)\n\n([\s\S]*)$/);
  if (!match) return { filename: null, text: content };
  return { filename: match[1], text: match[2] };
}

export function clipText(value: string, max = 72): string {
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function toolErrorMessage(payload: ToolPayload | null): string | null {
  return payload ? resultError(payload.result) : null;
}
