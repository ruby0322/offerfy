import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JobBoardPage from "@/components/jobs/JobBoardPage";
import { boardBySlug, boardQuery, JOB_BOARDS } from "@/lib/job-boards";
import { resolveLocale } from "@/lib/locale";
import {
  fetchJobCitiesServer,
  fetchJobCount,
  fetchJobFacets,
  fetchJobList,
  fetchJobPulse,
  type JobQuery,
} from "@/lib/jobs";
import { pageMetadata } from "@/lib/seo";

type RouteProps = {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function first(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

/** What a visitor may narrow a board by — never the keys the board owns. */
export function visitorQueryFrom(
  raw: Record<string, string | string[] | undefined>,
  locked: (keyof JobQuery)[],
): JobQuery {
  const query: JobQuery = {
    q: first(raw.q),
    remote: first(raw.remote),
    company: first(raw.company),
    country: first(raw.country),
    region: first(raw.region),
    city: first(raw.city),
    seniority: first(raw.seniority),
    function: first(raw.function),
    postedWithinDays: first(raw.postedWithinDays),
  };
  // A board's own axis cannot be overridden from the URL: /jobs/internships
  // ?seniority=senior would be a page whose h1 and its list disagreed.
  for (const key of locked) delete query[key];
  return query;
}

export async function boardCounts(): Promise<Record<string, number | undefined>> {
  const counts = await Promise.all(
    JOB_BOARDS.map(async (board) => [board.slug, await fetchJobCount(board.query)] as const),
  );
  return Object.fromEntries(counts);
}

/**
 * One route module for every curated board.
 *
 * Next resolves a static path segment ahead of the dynamic [id] one, so each
 * board gets a clean /jobs/<slug> URL that cannot collide with a posting —
 * at the cost of one two-line file per board, which is what this factory
 * fills in.
 */
export function boardRoute(slug: string) {
  async function generateMetadata({ params }: RouteProps): Promise<Metadata> {
    const { locale: localeParam } = await params;
    const locale = resolveLocale(localeParam);
    const board = boardBySlug(slug);
    if (!board) return {};
    const t = await getTranslations({ locale, namespace: "jobs" });
    const title = t(board.titleKey);
    return pageMetadata({
      locale,
      href: `/jobs/${slug}`,
      title: t("boardMetaTitle", { board: title }),
      description: t(board.leadKey),
    });
  }

  async function Page({ params, searchParams }: RouteProps) {
    const { locale: localeParam } = await params;
    const locale = resolveLocale(localeParam);
    setRequestLocale(locale);
    const board = boardBySlug(slug);
    if (!board) notFound();

    const raw = await searchParams;
    const visitor = visitorQueryFrom(raw, board.locked);
    const query = boardQuery(board, visitor);

    const [list, facets, pulse, cities, counts] = await Promise.all([
      fetchJobList(query),
      fetchJobFacets(query),
      fetchJobPulse(query),
      // Only the board that asks for a city accent pays for the query.
      board.accent === "cities" && board.query.country
        ? fetchJobCitiesServer(board.query.country)
        : Promise.resolve([]),
      boardCounts(),
    ]);

    return (
      <div className="landing-page">
        <Nav variant="landing" />
        <main>
          <JobBoardPage
            locale={locale}
            board={board}
            query={query}
            visitorQuery={visitor}
            list={list}
            facets={facets}
            pulse={pulse}
            cities={cities}
            boardCounts={counts}
          />
        </main>
        <Footer variant="landing" />
      </div>
    );
  }

  return { generateMetadata, Page };
}
