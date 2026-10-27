import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import PlanView from "@/components/PlanView";
import { resolveLocale } from "@/lib/locale";

type Props = {
  params: Promise<{ locale: string }>;
};

/**
 * Noindex, and no canonical or alternates.
 *
 * Unlike /usage and /pricing, which publish the same tier table to everyone
 * and are meant to be found, this page is one person's balance. There is
 * nothing here for a crawler to have, and `pageMetadata` would advertise it in
 * three locales. app/robots.ts disallows the path as well; this is the half
 * that holds if someone links to it.
 */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "plan" });
  return {
    title: t("metaTitle"),
    robots: { index: false, follow: false },
  };
}

export default async function PlanRoute({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <PlanView />;
}
