import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import CompanyWall from "@/components/landing/CompanyWall";
import Faq from "@/components/landing/Faq";
import Features from "@/components/landing/Features";
import FinalCta from "@/components/landing/FinalCta";
import Hero from "@/components/landing/Hero";
import TheLoop from "@/components/landing/TheLoop";
import JsonLd from "@/components/seo/JsonLd";
import { setRequestLocale } from "next-intl/server";
import { resolveLocale } from "@/lib/locale";
import { organizationJsonLd } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export default async function LandingPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(resolveLocale(locale));

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <Hero />
        <CompanyWall />
        <TheLoop />
        {/* These four are what each step of the loop actually runs on. The
            questions come after them because they are all follow-ups to a
            claim made above, and the CTA comes last because it is the only
            thing on the page asking for something. */}
        <Features />
        <Faq />
        <FinalCta />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
