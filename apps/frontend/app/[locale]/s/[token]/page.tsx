import ShareView from "@/components/share/ShareView";
import type { Metadata } from "next";
import { setRequestLocale } from "next-intl/server";
import { resolveLocale } from "@/lib/locale";

type Props = {
  params: Promise<{ locale: string; token: string }>;
};

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function SharePage({ params }: Props) {
  const { locale: localeParam, token } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <ShareView token={token} />;
}
