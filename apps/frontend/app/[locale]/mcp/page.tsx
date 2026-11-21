import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import McpGuide from "@/components/mcp/McpGuide";
import { resolveLocale } from "@/lib/locale";
import { pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "mcp" });
  return pageMetadata({
    locale,
    href: "/mcp",
    title: t("metaTitle"),
    description: t("metaDescription"),
  });
}

export default async function McpPage({ params }: Props) {
  const { locale: localeParam } = await params;
  setRequestLocale(resolveLocale(localeParam));
  return <McpGuide />;
}
