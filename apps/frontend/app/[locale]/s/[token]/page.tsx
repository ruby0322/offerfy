import ShareView from "@/components/share/ShareView";
import type { Metadata, ResolvingMetadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { resolveLocale } from "@/lib/locale";
import { pageUrl, publicShareExists, shareOgImageUrl, SITE_NAME } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string; token: string }>;
};

export async function generateMetadata(
  { params }: Props,
  parent: ResolvingMetadata,
): Promise<Metadata> {
  const { locale: localeParam, token } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "share" });
  const title = t("metaTitle");
  const description = t("metaDescription");
  const exists = await publicShareExists(token);
  const canonical = pageUrl(`/s/${token}`, locale);
  const parentMeta = await parent;
  const liveImage = {
    url: shareOgImageUrl(token),
    width: 1200,
    height: 630,
    alt: SITE_NAME,
  };
  const ogImages = exists ? [liveImage] : parentMeta.openGraph?.images;
  const twitterImages = exists ? [liveImage.url] : parentMeta.twitter?.images;
  return {
    title,
    description,
    robots: { index: false, follow: false },
    alternates: { canonical },
    openGraph: {
      title,
      description,
      siteName: SITE_NAME,
      url: canonical,
      ...(ogImages ? { images: ogImages } : {}),
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      ...(twitterImages ? { images: twitterImages } : {}),
    },
  };
}

export default async function SharePage({ params }: Props) {
  const { locale: localeParam, token } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <ShareView token={token} />;
}
