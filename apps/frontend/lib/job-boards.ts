import type { JobsMessageKey } from "@/components/jobs/facet-labels";
import type { JobQuery } from "@/lib/jobs";

/**
 * Curated boards: a filter, given a page of its own.
 *
 * They are pages rather than links into /jobs?seniority=intern because a page
 * can carry an h1, a description, a sitemap entry and a JobPosting listing —
 * "實習 職缺" and "new grad software engineer" are things people search for,
 * and a query string cannot be the answer to a search.
 *
 * Adding one is an entry here plus three message keys in each locale, and a
 * two-line route file under app/[locale]/jobs/<slug>. The route file exists
 * because Next resolves a static segment ahead of the dynamic [id] one, which
 * is what lets /jobs/internships and /jobs/<uuid> coexist.
 */
export type BoardAccent = "employers" | "cities" | "functions";

export type JobBoard = {
  slug: string;
  titleKey: JobsMessageKey;
  leadKey: JobsMessageKey;
  /** What the board *is*. Merged under anything the visitor then sets. */
  query: JobQuery;
  /** Query keys the board owns: their facet leaves the filter bar. */
  locked: (keyof JobQuery)[];
  accent: BoardAccent;
};

export const JOB_BOARDS: JobBoard[] = [
  {
    slug: "internships",
    titleKey: "boardInternshipsTitle",
    leadKey: "boardInternshipsLead",
    query: { seniority: "intern" },
    locked: ["seniority"],
    accent: "employers",
  },
  {
    // Two rungs, not one: the "new grad" facet only catches titles that say
    // so, and a lot of genuinely entry-level roles are tagged junior. One
    // rung would have made this board look empty next to the others.
    slug: "new-grad",
    titleKey: "boardNewGradTitle",
    leadKey: "boardNewGradLead",
    query: { seniority: "new_grad,junior" },
    locked: ["seniority"],
    accent: "employers",
  },
  {
    slug: "ready-to-move",
    titleKey: "boardReadyToMoveTitle",
    leadKey: "boardReadyToMoveLead",
    query: { seniority: "mid,senior,staff_plus" },
    locked: ["seniority"],
    accent: "functions",
  },
  {
    // Honest caveat worth knowing: this reads the employer's own remote flag,
    // which some boards never set, so it under-counts rather than guessing
    // from the description.
    slug: "remote",
    titleKey: "boardRemoteTitle",
    leadKey: "boardRemoteLead",
    query: { remote: "true" },
    locked: ["remote"],
    accent: "functions",
  },
  {
    slug: "ai",
    titleKey: "boardAiTitle",
    leadKey: "boardAiLead",
    query: { function: "ml" },
    locked: ["function"],
    accent: "employers",
  },
  {
    slug: "taiwan",
    titleKey: "boardTaiwanTitle",
    leadKey: "boardTaiwanLead",
    query: { country: "TW" },
    locked: ["country"],
    accent: "cities",
  },
];

export function boardBySlug(slug: string): JobBoard | null {
  return JOB_BOARDS.find((board) => board.slug === slug) ?? null;
}

/** The board's own filters, plus whatever the visitor set on top. */
export function boardQuery(board: JobBoard, visitor: JobQuery): JobQuery {
  return { ...visitor, ...board.query };
}

export function boardHref(board: JobBoard): string {
  return `/jobs/${board.slug}`;
}
