"use client";

import { useLocale, useTranslations } from "next-intl";
import { Check, ChevronDown, ClipboardList, FilePenLine, FileSearch, Globe, Paperclip, Redo2, Undo2, Wrench, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/api";
import MarkdownBody from "@/components/editor/MarkdownBody";
import EditDiff from "@/components/editor/EditDiff";
import {
  clipText,
  editCheckpointAction,
  parseAttachedUserMessage,
  parseToolPayload,
  toolErrorMessage,
  type ToolPayload,
} from "@/lib/chat-tools";
import { diffFromEditPayload } from "@/lib/edit-diff";

type Props = {
  message: ChatMessage;
  sending?: boolean;
  currentSource?: string;
  onRestoreEdit?: (source: string) => void;
};

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function atsCounts(result: unknown): { passed: number; total: number } | null {
  if (!result || typeof result !== "object") return null;
  const rec = result as { passed?: unknown; total?: unknown; checks?: unknown };
  const total =
    typeof rec.total === "number"
      ? rec.total
      : Array.isArray(rec.checks)
        ? rec.checks.length
        : null;
  const passed = typeof rec.passed === "number" ? rec.passed : null;
  if (passed == null || total == null) return null;
  return { passed, total };
}

function sourceLength(result: unknown): number | null {
  if (!result || typeof result !== "object") return null;
  const source = (result as { source?: unknown }).source;
  return typeof source === "string" ? source.length : null;
}

type Translate = (key: string, values?: Record<string, string | number>) => string;

function toolHeadline(payload: ToolPayload | null, t: Translate): {
  title: string;
  detail: string | null;
  failed: boolean;
  showCheck: boolean;
} {
  if (!payload) {
    return { title: t("toolUnknown", { name: "tool" }), detail: null, failed: false, showCheck: false };
  }
  const error = toolErrorMessage(payload);
  if (payload.name === "read_typst") {
    const chars = sourceLength(payload.result);
    return {
      title: t("toolRead"),
      detail: chars != null ? t("toolReadHint", { count: chars }) : t("toolReadHintPlain"),
      failed: Boolean(error),
      showCheck: !error,
    };
  }
  if (payload.name === "read_ats") {
    const counts = atsCounts(payload.result);
    return {
      title: t("toolAts"),
      detail: counts
        ? counts.passed === counts.total
          ? t("toolAtsAllPass")
          : t("toolAtsHint", { passed: counts.passed, total: counts.total })
        : null,
      failed: Boolean(error),
      showCheck: !error,
    };
  }
  if (payload.name === "web_search") {
    const query = asString(payload.arguments.query);
    return {
      title: t("toolSearch"),
      detail: query ? t("toolSearchQuery", { query: clipText(query, 80) }) : t("toolSearchPlain"),
      failed: Boolean(error),
      showCheck: !error,
    };
  }
  if (payload.name === "apply_typst_edit") {
    if (error) {
      return { title: t("toolEditFailed"), detail: error, failed: true, showCheck: false };
    }
    const result = payload.result;
    const changed =
      result && typeof result === "object" && "changed" in result
        ? Boolean((result as { changed: unknown }).changed)
        : null;
    if (changed === false) {
      return { title: t("toolEditNoChange"), detail: null, failed: false, showCheck: false };
    }
    const search = asString(payload.arguments.search);
    const replace = asString(payload.arguments.replace);
    if (search && replace && search.length <= 80 && replace.length <= 80) {
      return {
        title: t("toolEdit"),
        detail: t("toolEditReplace", { search: clipText(search, 48), replace: clipText(replace, 48) }),
        failed: false,
        showCheck: true,
      };
    }
    const start = payload.arguments.start;
    const end = payload.arguments.end;
    if (typeof start === "number" && typeof end === "number") {
      return {
        title: t("toolEdit"),
        detail: t("toolEditRange", { start, end }),
        failed: false,
        showCheck: true,
      };
    }
    return { title: t("toolEdit"), detail: null, failed: false, showCheck: true };
  }
  return {
    title: t("toolUnknown", { name: payload.name }),
    detail: error,
    failed: Boolean(error),
    showCheck: !error,
  };
}

function ToolIcon({ name, failed }: { name: string; failed: boolean }) {
  const className = "h-3.5 w-3.5 shrink-0";
  if (failed) return <X className={className} aria-hidden="true" />;
  if (name === "read_typst") return <FileSearch className={className} aria-hidden="true" />;
  if (name === "read_ats") return <ClipboardList className={className} aria-hidden="true" />;
  if (name === "web_search") return <Globe className={className} aria-hidden="true" />;
  if (name === "apply_typst_edit") return <FilePenLine className={className} aria-hidden="true" />;
  return <Wrench className={className} aria-hidden="true" />;
}

function sourceLinks(payload: ToolPayload): { url: string; title: string }[] {
  const result = payload.result;
  if (!result || typeof result !== "object") return [];
  const raw = (result as { sources?: unknown }).sources;
  if (!Array.isArray(raw)) return [];
  const links: { url: string; title: string }[] = [];
  for (const entry of raw) {
    if (typeof entry === "string" && entry) {
      links.push({ url: entry, title: entry });
      continue;
    }
    if (entry && typeof entry === "object") {
      const url = (entry as { url?: unknown }).url;
      const title = (entry as { title?: unknown }).title;
      if (typeof url === "string" && url) {
        links.push({ url, title: typeof title === "string" && title.trim() ? title.trim() : url });
      }
    }
  }
  return links;
}

function ToolDetails({ payload }: { payload: ToolPayload }) {
  const t = useTranslations("editor");
  const search = asString(payload.arguments.search);
  const replace = asString(payload.arguments.replace);
  const error = toolErrorMessage(payload);
  const chars = sourceLength(payload.result);
  const links = sourceLinks(payload);

  return (
    <div className="mt-2 space-y-2 border-t border-gray-200/80 pt-2 text-[11px] leading-relaxed text-gray-600 dark:border-gray-600 dark:text-gray-300">
      {search ? (
        <p>
          <span className="font-medium text-gray-500 dark:text-gray-400">{t("toolFrom")} </span>
          {clipText(search, 160)}
        </p>
      ) : null}
      {replace ? (
        <p>
          <span className="font-medium text-gray-500 dark:text-gray-400">{t("toolTo")} </span>
          {clipText(replace, 160)}
        </p>
      ) : null}
      {chars != null ? <p>{t("toolReadHint", { count: chars })}</p> : null}
      {links.length > 0 ? (
        <ul className="space-y-1">
          {links.map((link) => (
            <li key={link.url}>
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all underline decoration-gray-400 underline-offset-2 hover:text-cyan-700 dark:hover:text-cyan-300"
              >
                {clipText(link.title, 80)}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="text-red-600 dark:text-red-400">{error}</p> : null}
    </div>
  );
}

function RestoreButton({
  disabled,
  kind,
  label,
  onClick,
}: {
  disabled: boolean;
  kind: "restore" | "reapply";
  label: string;
  onClick: () => void;
}) {
  const Icon = kind === "reapply" ? Redo2 : Undo2;
  return (
    <button
      type="button"
      data-testid={kind === "reapply" ? "tool-edit-reapply" : "tool-edit-restore"}
      disabled={disabled}
      className="tool-edit-action inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:pointer-events-none disabled:opacity-50 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
      onClick={onClick}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </button>
  );
}

function ToolCallCard({
  message,
  sending = false,
  currentSource = "",
  onRestoreEdit,
}: {
  message: ChatMessage;
  sending?: boolean;
  currentSource?: string;
  onRestoreEdit?: (source: string) => void;
}) {
  const t = useTranslations("editor");
  const payload = parseToolPayload(message.content);
  const { title, detail, failed, showCheck } = toolHeadline(payload, t as Translate);
  const name = payload?.name ?? "tool";
  const checkpoint = onRestoreEdit ? editCheckpointAction(payload, currentSource) : null;
  const actionButton =
    checkpoint && onRestoreEdit ? (
      <RestoreButton
        disabled={sending}
        kind={checkpoint.kind}
        label={checkpoint.kind === "reapply" ? t("toolEditReapply") : t("toolEditRestore")}
        onClick={() => onRestoreEdit(checkpoint.source)}
      />
    ) : null;
  const diffLines =
    payload && payload.name === "apply_typst_edit" && !failed ? diffFromEditPayload(payload) : [];
  const showDetails = Boolean(
    payload &&
      payload.name !== "apply_typst_edit" &&
      payload.name !== "read_typst" &&
      payload.name !== "read_ats" &&
      (asString(payload.arguments.search) ||
        asString(payload.arguments.replace) ||
        asString(payload.arguments.query) ||
        sourceLinks(payload).length > 0 ||
        toolErrorMessage(payload)),
  );

  return (
    <div className="flex w-full justify-start">
      <div
        className={cn(
          "w-full max-w-[90vw] rounded-lg border px-3 py-2 text-sm",
          failed
            ? "border-red-200 bg-red-50 text-red-900 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-100"
            : "border-gray-200 bg-gray-50 text-gray-800 dark:border-gray-700 dark:bg-gray-800/70 dark:text-gray-100",
        )}
      >
        <p className="flex items-center gap-2 font-medium tracking-tight">
          <span
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full",
              failed
                ? "bg-red-100 text-red-700 dark:bg-red-900/80 dark:text-red-100"
                : "bg-white text-gray-600 dark:bg-gray-900 dark:text-gray-300",
            )}
          >
            <ToolIcon name={name} failed={failed} />
          </span>
          <span className="min-w-0 flex-1">{title}</span>
          {showCheck ? (
            <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
          ) : null}
        </p>
        {detail && diffLines.length === 0 ? (
          <p className="mt-1 pl-8 text-xs text-gray-600 dark:text-gray-300">{detail}</p>
        ) : null}
        {diffLines.length > 0 ? (
          <div className="mt-1 pl-8">
            <EditDiff lines={diffLines} action={actionButton} />
          </div>
        ) : actionButton ? (
          <div className="mt-1 flex pl-8">
            <div className="ml-auto">{actionButton}</div>
          </div>
        ) : null}
        {showDetails && payload ? (
          <details className="group pl-8">
            <summary className="mt-1 flex cursor-pointer list-none items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200">
              <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" aria-hidden="true" />
              {t("toolDetails")}
            </summary>
            <ToolDetails payload={payload} />
          </details>
        ) : null}
      </div>
    </div>
  );
}

export default function ChatMessageCard({ message, sending, currentSource, onRestoreEdit }: Props) {
  const locale = useLocale();
  const isUser = message.role === "user";
  const time = message.timestamp ? new Date(message.timestamp) : null;

  if (message.role === "tool") {
    return (
      <ToolCallCard
        message={message}
        sending={sending}
        currentSource={currentSource}
        onRestoreEdit={onRestoreEdit}
      />
    );
  }

  if (!isUser) {
    return (
      <div className="w-full max-w-none text-gray-900 dark:text-gray-100">
        <MarkdownBody text={message.content} />
      </div>
    );
  }

  const { filename, text } = parseAttachedUserMessage(message.content);

  return (
    <div className="flex w-full justify-end">
      <div className="w-fit max-w-[min(90%,32rem)] rounded-2xl bg-cyan-600 px-4 py-2 text-white">
        {filename ? (
          <p className="mb-1.5 flex items-center gap-1.5 text-xs text-white/90">
            <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 truncate">{filename}</span>
          </p>
        ) : null}
        {text ? (
          <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">{text}</p>
        ) : null}
        {time && !Number.isNaN(time.getTime()) ? (
          <p className="mt-1 text-xs text-white/70">
            {time.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}
          </p>
        ) : null}
      </div>
    </div>
  );
}
