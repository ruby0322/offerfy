import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import LegalDocument from "@/components/legal/LegalDocument";
import { resolveLocale } from "@/lib/locale";
import { EMPLOYER_COUNT, pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "legal.terms" });
  const meta = await getTranslations({ locale, namespace: "meta" });
  return pageMetadata({
    locale,
    href: "/terms",
    title: t("metaTitle"),
    description: meta("description", { count: EMPLOYER_COUNT }),
  });
}

export default async function TermsPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <LegalDocument doc="terms" />;
}
