import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import EmployerIndex from "@/components/jobs/EmployerIndex";
import { EMPLOYERS_HREF } from "@/lib/employers";
import { resolveLocale } from "@/lib/locale";
import { fetchJobEmployers } from "@/lib/jobs";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "jobs" });
  return pageMetadata({
    locale,
    href: EMPLOYERS_HREF,
    title: t("boardMetaTitle", { board: t("employersTitle") }),
    description: t("employersLead"),
  });
}

/**
 * /jobs/employers -- a static segment, so Next resolves this ahead of
 * /jobs/[id], the same reason every curated board has a route file.
 */
export default async function EmployersPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const { items, total } = await fetchJobEmployers();

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <main>
        <EmployerIndex employers={items} total={total} />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
