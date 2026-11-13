import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { JOB_BOARDS, boardHref, type JobBoard } from "@/lib/job-boards";

type Props = {
  /** Open roles per board slug, or undefined when a count is unavailable. */
  counts: Record<string, number | undefined>;
  /** Shown on a board page as "other boards": the current one is dropped. */
  exclude?: string;
  compact?: boolean;
};

export default async function CuratedBoards({ counts, exclude, compact }: Props) {
  const t = await getTranslations("jobs");
  const boards: JobBoard[] = JOB_BOARDS.filter((board) => board.slug !== exclude);
  if (boards.length === 0) return null;

  return (
    <section className={`jobs-curated${compact ? " is-compact" : ""}`}>
      <h2 className="jobs-section-label">
        {compact ? t("boardsOther") : t("curatedLabel")}
      </h2>
      <div className="jobs-curated-grid">
        {boards.map((board) => {
          const count = counts[board.slug];
          return (
            <Link key={board.slug} href={boardHref(board)} className="jobs-collection">
              <span className="jobs-collection-name">{t(board.titleKey)}</span>
              {compact ? null : (
                <span className="jobs-collection-desc">{t(board.leadKey)}</span>
              )}
              {typeof count === "number" ? (
                <span className="jobs-collection-count">
                  {t("boardCount", { count })}
                </span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </section>
  );
}
