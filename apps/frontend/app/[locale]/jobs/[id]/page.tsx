import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JobView from "@/components/jobs/JobView";
import JsonLd from "@/components/seo/JsonLd";
import { resolveLocale } from "@/lib/locale";
import { excerpt, fetchJob } from "@/lib/jobs";
import { jobPostingJsonLd, pageMetadata, SITE_NAME } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string; id: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam, id } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "jobs" });
  const job = await fetchJob(id);
  if (!job) {
    return {};
  }
  const href = `/jobs/${job.id}`;
  const description = excerpt(job.description_text) || t("metaDescription");
  return {
    ...pageMetadata({
      locale,
      href,
      title: `${job.title} · ${job.company} · ${SITE_NAME}`,
      description,
    }),
    robots: job.is_active ? undefined : { index: false, follow: false },
  };
}

export default async function JobDetailPage({ params }: Props) {
  const { locale: localeParam, id } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const job = await fetchJob(id);
  if (!job) {
    notFound();
  }
  const href = `/jobs/${job.id}`;
  const jsonLd = job.is_active
    ? jobPostingJsonLd({
        locale,
        href,
        jobId: job.id,
        title: job.title,
        descriptionHtml: job.description_html,
        descriptionText: job.description_text,
        company: job.company,
        location: job.location,
        remote: job.remote,
        datePosted: job.posted_at ?? job.first_seen_at,
        lastSeenAt: job.last_seen_at,
      })
    : null;

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      {jsonLd ? <JsonLd data={jsonLd} /> : null}
      <main>
        <JobView locale={locale} job={job} />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
