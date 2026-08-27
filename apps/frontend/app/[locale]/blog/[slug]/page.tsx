import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PostArticle from "@/components/blog/PostArticle";
import JsonLd from "@/components/seo/JsonLd";
import { getPost, listSlugs, localeCopy } from "@/lib/blog/load";
import { resolveLocale } from "@/lib/locale";
import { blogPostingJsonLd, pageMetadata, SITE_NAME } from "@/lib/seo";

type Props = {
  params: Promise<{ locale: string; slug: string }>;
};

export function generateStaticParams() {
  return listSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale: localeParam, slug } = await params;
  const locale = resolveLocale(localeParam);
  const post = getPost(slug);
  if (!post) {
    return {};
  }
  const copy = localeCopy(post, locale);
  const href = `/blog/${post.slug}`;
  return pageMetadata({
    locale,
    href,
    title: `${copy.title} · ${SITE_NAME}`,
    description: copy.description,
    type: "article",
    publishedTime: post.publishedAt,
    modifiedTime: post.updatedAt ?? post.publishedAt,
  });
}

export default async function BlogPostPage({ params }: Props) {
  const { locale: localeParam, slug } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const post = getPost(slug);
  if (!post) {
    notFound();
  }
  const copy = localeCopy(post, locale);
  const href = `/blog/${post.slug}`;

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd
        data={blogPostingJsonLd({
          locale,
          href,
          title: copy.title,
          description: copy.description,
          publishedAt: post.publishedAt,
          updatedAt: post.updatedAt,
        })}
      />
      <main>
        <PostArticle post={post} locale={locale} />
      </main>
      <Footer variant="landing" />
    </div>
  );
}
