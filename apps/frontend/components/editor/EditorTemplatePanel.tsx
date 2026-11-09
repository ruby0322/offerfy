"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { listTemplates, templatePreviewUrl, type UniverseTemplate } from "@/lib/api";
import { templateFieldKey } from "@/lib/chat-tools";
import { cn } from "@/lib/utils";

/** Which half of the list the carousel shows.
 *
 * The two differ in what pressing Apply costs, and in nothing else a
 * thumbnail can convey: an `instant` template has an emitter server-side, so
 * the document is rendered from the stored content snapshot with no model
 * call at all, while a `rewrite` template goes through a chat turn that
 * rewrites the whole file. Both used to sit in one undifferentiated strip,
 * which left pressing the button as the only way to find out which kind you
 * had picked. */
type TemplateGroup = "instant" | "rewrite";

/** What an applied switch left behind, for the inline note below --
 * `null` when the switch went through the model path instead, or failed,
 * so there is nothing of this shape to show. */
export type AppliedTemplateNote = { name: string; version: string; dropped: string[]; degraded: string[] };

type Props = {
  sending: boolean;
  onApply: (template: { name: string; version: string }) => Promise<AppliedTemplateNote | null>;
};

export default function EditorTemplatePanel({ sending, onApply }: Props) {
  const t = useTranslations("editor");
  const [templates, setTemplates] = useState<UniverseTemplate[]>([]);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  const [appliedNote, setAppliedNote] = useState<AppliedTemplateNote | null>(null);
  const [group, setGroup] = useState<TemplateGroup>("instant");
  const stripRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    listTemplates()
      .then((rows) => {
        if (!cancelled) {
          setTemplates(rows);
          setIndex(0);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError(t("templateLoadError"));
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  const instant = templates.filter((row) => row.deterministic);
  const rewrite = templates.filter((row) => !row.deterministic);
  const visible = group === "instant" ? instant : rewrite;
  const current = visible[index] ?? null;
  const count = visible.length;

  const selectGroup = useCallback((next: TemplateGroup) => {
    setGroup(next);
    // The carousel indexes into the filtered list, so an index carried over
    // from a longer group would point at a different template -- or past the
    // end of a shorter one.
    setIndex(0);
    // The note names the template a previous Apply wrote. Carrying it across
    // a group change would leave it describing something the carousel is no
    // longer showing.
    setAppliedNote(null);
  }, []);

  const go = useCallback(
    (next: number) => {
      if (count === 0) return;
      setIndex(((next % count) + count) % count);
    },
    [count],
  );

  useEffect(() => {
    const selected = stripRef.current?.querySelector("[data-active='true']");
    if (selected instanceof HTMLElement) {
      selected.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
    }
  }, [index]);

  if (!loaded) {
    return (
      <div className="flex h-full items-center justify-center p-3">
        <p className="text-sm text-muted-foreground">{t("templateLoading")}</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-3">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }
  // Only when the server offered nothing at all. An empty *group* keeps the
  // whole panel, tabs included, and says so in the body below -- replacing
  // the panel there would hide the only control that gets you back out.
  if (templates.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-3">
        <p className="text-sm text-muted-foreground">{t("templateEmpty")}</p>
      </div>
    );
  }

  const previewSrc = current ? templatePreviewUrl(current.name) : null;
  const showImage = current ? !broken[current.name] : false;

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      data-slot="template-viewer"
      className="flex h-full min-h-0 flex-col gap-2 p-3 outline-none"
      onKeyDown={(event) => {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          go(index - 1);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          go(index + 1);
        }
      }}
    >
      <Tabs value={group} onValueChange={(next) => selectGroup(next as TemplateGroup)}>
        <TabsList className="w-full">
          <TabsTrigger value="instant" className="flex-1">
            {t("templateGroupInstant", { count: instant.length })}
          </TabsTrigger>
          <TabsTrigger value="rewrite" className="flex-1">
            {t("templateGroupRewrite", { count: rewrite.length })}
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <p className="shrink-0 text-[11px] leading-relaxed text-muted-foreground">
        {group === "instant" ? t("templateGroupInstantHint") : t("templateGroupRewriteHint")}
      </p>

      {current ? (
      <header className="flex shrink-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold text-foreground">
            {current.name}
          </h2>
          <p className="text-[11px] text-muted-foreground">
            {current.version}
            <span className="px-1">·</span>
            {t("templateCount", { current: index + 1, total: count })}
            <span className="px-1">·</span>
            <a
              href={current.universe_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-clay underline-offset-2 hover:underline"
            >
              {t("templateUniverse")}
            </a>
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          disabled={sending}
          className="shrink-0"
          onClick={() => {
            setAppliedNote(null);
            void onApply({ name: current.name, version: current.version }).then(setAppliedNote);
          }}
        >
          {t("templateApply")}
        </Button>
      </header>
      ) : null}

      {appliedNote ? (
        <div
          data-testid="template-applied-note"
          className="shrink-0 rounded-lg border border-border bg-sheet px-3 py-2 text-xs text-muted-foreground"
        >
          <p className="font-medium text-foreground">
            {t("templateSwitchApplied", { name: appliedNote.name, version: appliedNote.version })}
          </p>
          {appliedNote.dropped.length > 0 || appliedNote.degraded.length > 0 ? (
            <ul className="mt-1 space-y-0.5">
              {appliedNote.dropped.map((path) => (
                <li key={`dropped-${path}`}>
                  {t("templateSwitchDropped", { field: t(templateFieldKey(path)) })}
                </li>
              ))}
              {appliedNote.degraded.map((path) => (
                <li key={`degraded-${path}`}>
                  {t("templateSwitchDegraded", { field: t(templateFieldKey(path)) })}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg bg-paper">
        {!current ? (
          <p className="px-4 text-center text-sm text-muted-foreground">
            {t("templateGroupEmpty")}
          </p>
        ) : showImage && previewSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={current.name}
            src={previewSrc}
            alt={t("templatePreview", { name: current.name })}
            className="max-h-full max-w-full object-contain"
            onError={() => setBroken((prev) => ({ ...prev, [current.name]: true }))}
          />
        ) : (
          <p className="px-4 text-center text-sm text-muted-foreground">{current.name}</p>
        )}
      </div>

      {current?.description ? (
        <p className="line-clamp-3 shrink-0 text-xs leading-relaxed text-muted-foreground">
          {current.description}
        </p>
      ) : null}

      <nav className="flex shrink-0 items-center gap-1" aria-label={t("tabTemplate")}>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0"
          aria-label={t("templatePrev")}
          onClick={() => go(index - 1)}
        >
          <ChevronLeft />
        </Button>
        <div
          ref={stripRef}
          className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto py-0.5 [scrollbar-width:thin]"
        >
          {visible.map((row, i) => {
            const active = i === index;
            return (
              <button
                key={`${row.name}-${row.version}`}
                type="button"
                data-active={active ? "true" : "false"}
                className={cn(
                  "h-16 w-12 shrink-0 overflow-hidden rounded border bg-sheet",
                  active ? "border-clay ring-2 ring-clay/40" : "border-rule",
                )}
                aria-label={row.name}
                aria-current={active ? "true" : undefined}
                onClick={() => setIndex(i)}
              >
                {broken[row.name] ? (
                  <span className="flex h-full items-center justify-center px-0.5 text-[8px] leading-tight text-muted-foreground">
                    {row.name}
                  </span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={templatePreviewUrl(row.name)}
                    alt=""
                    loading={Math.abs(i - index) <= 2 ? "eager" : "lazy"}
                    className="h-full w-full object-cover"
                    onError={() => setBroken((prev) => ({ ...prev, [row.name]: true }))}
                  />
                )}
              </button>
            );
          })}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0"
          aria-label={t("templateNext")}
          onClick={() => go(index + 1)}
        >
          <ChevronRight />
        </Button>
      </nav>
    </div>
  );
}
