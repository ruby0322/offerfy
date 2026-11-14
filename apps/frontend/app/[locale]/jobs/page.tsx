import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JobIndex from "@/components/jobs/JobIndex";
import { resolveLocale } from "@/lib/locale";
import { boardCounts, visitorQueryFrom } from "@/components/jobs/board-route";
import { fetchJobFacets, fetchJobList, fetchJobPulse, type JobQuery } from "@/lib/jobs";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "jobs" });
  return pageMetadata({
    locale,
    href: "/jobs",
    title: t("metaTitle"),
    description: t("metaDescription"),
  });
}

export default async function JobsPage({ params, searchParams }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const raw = await searchParams;
  // No locked keys here: the open catalogue owns none of them.
  const query: JobQuery = visitorQueryFrom(raw, []);
  const [list, facets, pulse, counts] = await Promise.all([
    fetchJobList(query),
    // Facet counts follow the current filters, so the menus describe the
    // list the visitor is looking at rather than the whole catalogue.
    fetchJobFacets(query),
    fetchJobPulse(),
    boardCounts(),
  ]);

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <main>
        <JobIndex
          locale={locale}
          query={query}
          list={list}
          facets={facets}
          pulse={pulse}
          boardCounts={counts}
        />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
