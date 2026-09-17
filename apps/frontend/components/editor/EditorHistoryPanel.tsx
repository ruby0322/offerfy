"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  getResumeRevision,
  listResumeRevisions,
  restoreResumeRevision,
  type Resume,
  type RevisionDetail,
  type RevisionListItem,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  resumeId: string;
  reloadKey?: string;
  onRestored: (resume: Resume) => void;
};

export default function EditorHistoryPanel({ resumeId, reloadKey, onRestored }: Props) {
  const t = useTranslations("editor");
  const locale = useLocale();
  const [rows, setRows] = useState<RevisionListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RevisionDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listResumeRevisions(resumeId)
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        setLoadError(null);
        setReady(true);
        setSelectedId((current) =>
          current && list.some((row) => row.id === current) ? current : (list[0]?.id ?? null),
        );
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : t("historyLoadError"));
          setReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resumeId, reloadKey, t]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    getResumeRevision(resumeId, selectedId)
      .then((row) => {
        if (!cancelled) setDetail(row);
      })
      .catch(() => {
        /* keep previous detail */
      });
    return () => {
      cancelled = true;
    };
  }, [resumeId, selectedId]);

  async function onRestore() {
    if (!selectedId || busy) return;
    if (!window.confirm(t("historyRestoreConfirm"))) return;
    setBusy(true);
    try {
      const saved = await restoreResumeRevision(resumeId, selectedId);
      const list = await listResumeRevisions(resumeId);
      setRows(list);
      setLoadError(null);
      onRestored(saved);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t("historyRestoreError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 sm:p-4">
        {loadError ? (
          <p className="text-sm text-muted-foreground">{loadError}</p>
        ) : null}
        {!ready && !loadError ? (
          <p className="text-sm text-muted-foreground">{t("historyLoading")}</p>
        ) : null}
        {ready && !loadError && rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("historyEmpty")}</p>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col gap-3 sm:flex-row">
          <ul className="max-h-40 shrink-0 overflow-y-auto sm:max-h-none sm:w-48">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full flex-col items-start gap-1 rounded-md px-2 py-2 text-left text-xs",
                    selectedId === row.id
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-accent/50",
                  )}
                  onClick={() => setSelectedId(row.id)}
                >
                  <span>
                    {new Date(row.created_at).toLocaleString(locale)}
                  </span>
                  {row.is_published ? (
                    <Badge variant="secondary">{t("historyPublished")}</Badge>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">{t("historySelected")}</span>
              <Button
                type="button"
                size="sm"
                disabled={!selectedId || busy}
                onClick={() => void onRestore()}
              >
                {t("historyRestore")}
              </Button>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto rounded-md border border-border bg-muted/30 p-3 text-xs leading-5">
              {detail && detail.id === selectedId ? detail.typst_source : ""}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
