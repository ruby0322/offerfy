import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import PricingPageView from "@/components/PricingPage";
import { resolveLocale } from "@/lib/locale";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "pricing" });
  return pageMetadata({
    locale,
    href: "/pricing",
    title: t("metaTitle"),
    description: t("metaDescription"),
  });
}

export default async function PricingRoute({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <PricingPageView />;
}
