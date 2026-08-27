import { setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import Hero from "@/components/landing/Hero";
import Reasons from "@/components/landing/Reasons";
import Roadmap from "@/components/landing/Roadmap";
import { resolveLocale } from "@/lib/locale";

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
      <main>
        <Hero />
        <Reasons />
        <Roadmap />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
