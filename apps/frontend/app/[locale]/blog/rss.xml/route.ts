import { NextResponse } from "next/server";
import { listPosts, localeCopy } from "@/lib/blog/load";
import { resolveLocale } from "@/lib/locale";
import { pageUrl, SITE_NAME } from "@/lib/seo";
import type { AppLocale } from "@/i18n/routing";

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function rssDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toUTCString();
}

type Props = {
  params: Promise<{ locale: string }>;
};

export async function GET(_request: Request, { params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  const posts = listPosts();
  const channelUrl = pageUrl("/blog", locale);
  const items = posts
    .map((post) => {
      const copy = localeCopy(post, locale);
      const link = pageUrl(`/blog/${post.slug}`, locale);
      return `    <item>
      <title>${escapeXml(copy.title)}</title>
      <link>${escapeXml(link)}</link>
      <guid>${escapeXml(link)}</guid>
      <pubDate>${rssDate(post.publishedAt)}</pubDate>
      <description>${escapeXml(copy.description)}</description>
    </item>`;
    })
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>${escapeXml(`${SITE_NAME} Blog`)}</title>
    <link>${escapeXml(channelUrl)}</link>
    <description>${escapeXml(`${SITE_NAME} notes and guides`)}</description>
    <language>${localeXml(locale)}</language>
${items}
  </channel>
</rss>
`;

  return new NextResponse(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}

function localeXml(locale: AppLocale): string {
  if (locale === "zh-TW") return "zh-Hant";
  if (locale === "zh-CN") return "zh-Hans";
  return "en";
}
