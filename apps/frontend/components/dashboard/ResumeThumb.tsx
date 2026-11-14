"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { apiJson } from "@/lib/api";

/**
 * The first page of a resume, as a thumbnail.
 *
 * A tailored resume is a document, and seven of them with similar names are
 * indistinguishable as text, so the block view shows the actual rendered
 * page. The cost is real and shapes this component:
 * `GET /v1/resumes/{id}/preview` compiles Typst on every call. It spends no
 * credits -- there is no model in that path, only CPU -- but twelve cards in
 * a grid would be twelve compiles on every visit.
 *
 * So two defences. One, nothing renders until the card is actually scrolled
 * into view. Two, a module-level cache keyed by resume id *and* the revision
 * stamp, so re-opening the tab in the same session is free while an edited
 * resume still re-renders. A server-side cache per revision is the real fix
 * and belongs in the API; this makes the grid usable without it.
 */

type PreviewPages = { pages: string[] };

/** id + updated_at -> first page SVG. Lives for the session, not the mount. */
const CACHE = new Map<string, string>();

type Props = {
  resumeId: string;
  /** Part of the cache key, so an edit invalidates its own thumbnail. */
  revision: string | null;
  title: string;
};

export default function ResumeThumb({ resumeId, revision, title }: Props) {
  const t = useTranslations("dashboard");
  const key = `${resumeId}:${revision ?? ""}`;
  // Read during render rather than synced into state by an effect: the cache
  // is the source of truth, and the fetch below is the only thing that needs
  // to announce a change. The parent keys this component by id and revision,
  // so a changed key remounts and no reset is needed.
  const cached = CACHE.get(key) ?? null;
  const [fetched, setFetched] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const svg = cached ?? fetched;

  useEffect(() => {
    if (CACHE.has(key)) return;
    const node = box.current;
    if (!node) return;
    let live = true;

    async function render() {
      try {
        const res = await apiJson<PreviewPages>(
          `/api/v1/resumes/${encodeURIComponent(resumeId)}/preview`,
        );
        const first = res.pages[0];
        if (!first) throw new Error("no pages");
        CACHE.set(key, first);
        if (live) setFetched(first);
      } catch {
        if (live) setFailed(true);
      }
    }

    // No IntersectionObserver (an old browser, a test environment) means
    // render immediately rather than never: a missing thumbnail is worse
    // than an eager compile.
    if (typeof IntersectionObserver === "undefined") {
      void render();
      return () => {
        live = false;
      };
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          observer.disconnect();
          void render();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => {
      live = false;
      observer.disconnect();
    };
  }, [key, resumeId]);

  return (
    <div
      ref={box}
      // A4 proportions, so the thumbnail is recognisably the page rather than
      // a crop of it. White in both themes: paper is paper.
      className="flex aspect-[1/1.414] items-center justify-center overflow-hidden border-b border-rule bg-white"
      aria-hidden="true"
    >
      {svg ? (
        <div
          className="h-full w-full [&>svg]:h-full [&>svg]:w-full [&>svg]:object-contain"
          // The API returns SVG it compiled itself from the person's own
          // Typst; the editor's own preview renders it the same way.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <span className="px-2 text-center text-[0.68rem] text-neutral-400">
          {failed ? t("thumbFailed") : t("thumbRendering")}
          <span className="sr-only">{title}</span>
        </span>
      )}
    </div>
  );
}
