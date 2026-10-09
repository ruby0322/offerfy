"use client";

import { useTranslations } from "next-intl";
import { BookmarkCheck, Trash2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { MemoryDecision, type FamilyCardActions } from "@/components/editor/ChatMessageCard";
import type { MemoryItem } from "@/lib/api";

type Props = {
  items: MemoryItem[];
  notice: string | null;
  actions: FamilyCardActions;
  onDeleteMemory: (id: string) => void;
};

export default function MemoryPanel({ items, notice, actions, onDeleteMemory }: Props) {
  const t = useTranslations("editor");
  const pending = items.filter((item) => item.status === "pending");
  const accepted = items.filter((item) => item.status === "accepted");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="memory-panel-trigger"
          aria-label={t("memoryPanel")}
          className="gap-1.5"
        >
          <BookmarkCheck aria-hidden="true" />
          <span className="hidden sm:inline">{t("memoryPanel")}</span>
          {pending.length > 0 ? (
            <span
              data-testid="memory-pending-count"
              className="min-w-4 rounded-full bg-primary px-1 text-[10px] leading-4 font-semibold text-primary-foreground"
            >
              {pending.length}
            </span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-[min(22rem,calc(100vw-1.5rem))] p-3"
        data-testid="memory-panel"
      >
        {notice ? <p className="mb-2 text-xs text-destructive">{notice}</p> : null}
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("memoryEmpty")}</p>
        ) : null}
        {pending.length > 0 ? (
          <section className="mb-3">
            <h3 className="mb-1.5 text-xs font-medium text-muted-foreground">
              {t("memoryPending")}
            </h3>
            <ul className="space-y-2">
              {pending.map((item) => (
                <li key={item.id} className="rounded-md border border-border px-2.5 py-2">
                  <p className="mb-1.5 text-sm break-words">{item.text}</p>
                  <MemoryDecision itemId={item.id} actions={actions} />
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {accepted.length > 0 ? (
          <section>
            <h3 className="mb-1.5 text-xs font-medium text-muted-foreground">
              {t("memoryAccepted")}
            </h3>
            <ul className="space-y-1">
              {accepted.map((item) => (
                <li key={item.id} className="flex items-start gap-2 text-sm">
                  <span className="min-w-0 flex-1 break-words">{item.text}</span>
                  {actions.signedIn ? (
                    <button
                      type="button"
                      data-testid="memory-delete"
                      aria-label={t("memoryDelete")}
                      disabled={actions.busyMemoryId === item.id}
                      className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                      onClick={() => onDeleteMemory(item.id)}
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
