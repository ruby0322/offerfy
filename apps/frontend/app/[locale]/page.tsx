import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Hero from "@/components/landing/Hero";
import JobsMarquee from "@/components/jobs/JobsMarquee";
import JsonLd from "@/components/seo/JsonLd";
import { resolveLocale } from "@/lib/locale";
import { fetchFeaturedJobs } from "@/lib/jobs";
import { organizationJsonLd } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export default async function LandingPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const t = await getTranslations("landing");
  const featured = await fetchFeaturedJobs();

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <Hero />
        {featured.length > 0 ? (
          <div className="mx-auto max-w-[72rem] px-5 pb-16">
            <JobsMarquee locale={locale} jobs={featured} label={t("featuredLabel")} />
          </div>
        ) : null}
      </main>
      <Footer variant="landing" />
    </div>
  );
}
