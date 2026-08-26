import { setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import HeroLoop from "@/components/landing/HeroLoop";
import HowItWorks from "@/components/landing/HowItWorks";
import NowNext from "@/components/landing/NowNext";
import Problem from "@/components/landing/Problem";
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
        <HeroLoop />
        <Problem />
        <HowItWorks />
        <NowNext />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
