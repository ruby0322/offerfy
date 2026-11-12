import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import CompanyWall from "@/components/landing/CompanyWall";
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
      </main>
      <Footer variant="landing" />
    </div>
  );
}
