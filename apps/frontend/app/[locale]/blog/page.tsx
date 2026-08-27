import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PostIndex from "@/components/blog/PostIndex";
import JsonLd from "@/components/seo/JsonLd";
import { resolveLocale } from "@/lib/locale";
import { blogJsonLd, pageMetadata } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const t = await getTranslations({ locale, namespace: "blog" });
  return pageMetadata({
    locale,
    href: "/blog",
    title: t("metaTitle"),
    description: t("metaDescription"),
  });
}

export default async function BlogIndexPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={blogJsonLd(locale)} />
      <main>
        <PostIndex locale={locale} />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
