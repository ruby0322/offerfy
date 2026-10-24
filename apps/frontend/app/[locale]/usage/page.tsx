import type { Metadata } from "next";
import { setRequestLocale, getTranslations } from "next-intl/server";
import UsageDocument from "@/components/UsageDocument";
import { resolveLocale } from "@/lib/locale";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "usage" });
  return pageMetadata({
    locale,
    href: "/usage",
    title: t("metaTitle"),
    description: t("metaDescription"),
  });
}

export default async function UsagePage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <UsageDocument />;
}
