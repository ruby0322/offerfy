import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import EmployerPage from "@/components/jobs/EmployerPage";
import { visitorQueryFrom } from "@/components/jobs/board-route";
import { EMPLOYERS_HREF } from "@/lib/employers";
import { resolveLocale } from "@/lib/locale";
import {
  fetchJobEmployer,
  fetchJobFacets,
  fetchJobList,
  type JobQuery,
} from "@/lib/jobs";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string; slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam, slug } = await params;
  const locale = resolveLocale(localeParam);
  const profile = await fetchJobEmployer(slug);
  if (!profile) return {};
  const t = await getTranslations({ locale, namespace: "jobs" });
  return pageMetadata({
    locale,
    href: `${EMPLOYERS_HREF}/${profile.slug}`,
    title: t("boardMetaTitle", {
      board: t("employerMetaTitle", { company: profile.company }),
    }),
    description: t("employerMetaDescription", { company: profile.company }),
  });
}

/**
 * One employer's page.
 *
 * Not prerendered: the employer set is a GROUP BY over live postings, so it
 * changes with every ingest pass, and a build-time list would go stale in
 * both directions. The backend answers the slug, and an employer with
 * nothing open is a 404 rather than an empty page.
 *
 * The company filter is the page's own axis and cannot be overridden from
 * the URL -- ?company= is stripped by visitorQueryFrom -- for the same
 * reason a curated board locks its own key: an h1 and a list that disagreed
 * would be a worse page than either alone.
 */
export default async function EmployerDetailPage({ params, searchParams }: Props) {
  const { locale: localeParam, slug } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);

  const profile = await fetchJobEmployer(slug);
  if (!profile) notFound();

  const raw = await searchParams;
  const visitor = visitorQueryFrom(raw, ["company"]);
  const query: JobQuery = { ...visitor, company: profile.company };

  const [list, facets] = await Promise.all([fetchJobList(query), fetchJobFacets(query)]);

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <main>
        <EmployerPage
          locale={locale}
          profile={profile}
          query={query}
          visitorQuery={visitor}
          list={list}
          facets={facets}
        />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
