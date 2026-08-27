import { setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Hero from "@/components/landing/Hero";
import Notes from "@/components/landing/Notes";
import Roadmap from "@/components/landing/Roadmap";
import JsonLd from "@/components/seo/JsonLd";
import { resolveLocale } from "@/lib/locale";
import { organizationJsonLd } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export default async function LandingPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <Hero />
        <Notes />
        <Roadmap />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
