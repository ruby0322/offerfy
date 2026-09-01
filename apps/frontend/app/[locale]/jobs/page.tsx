import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JobIndex from "@/components/jobs/JobIndex";
import { resolveLocale } from "@/lib/locale";
import { fetchFeaturedJobs, fetchJobList, type JobQuery } from "@/lib/jobs";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function first(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

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
  const query: JobQuery = {
    q: first(raw.q),
    source: first(raw.source),
    remote: first(raw.remote),
  };
  const [list, featured] = await Promise.all([fetchJobList(query), fetchFeaturedJobs()]);

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <main>
        <JobIndex locale={locale} query={query} list={list} featured={featured} />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
