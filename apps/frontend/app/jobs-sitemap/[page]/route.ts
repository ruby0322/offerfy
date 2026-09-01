import { fetchJobSitemapPage } from "@/lib/jobs";
import { languageAlternates, pageUrl } from "@/lib/seo";
import { routing } from "@/i18n/routing";

type Props = {
  params: Promise<{ page: string }>;
};

function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export async function GET(_request: Request, { params }: Props) {
  const raw = (await params).page.replace(/\.xml$/i, "");
  const page = Number.parseInt(raw, 10);
  if (!Number.isFinite(page) || page < 0) {
    return new Response("Not found", { status: 404 });
  }
  const data = await fetchJobSitemapPage(page);
  if (!data || (page > 0 && data.items.length === 0)) {
    return new Response("Not found", { status: 404 });
  }
  const lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
  ];
  for (const item of data.items) {
    const href = `/jobs/${item.id}`;
    const loc = pageUrl(href, routing.defaultLocale);
    const lastmod = item.last_seen_at.slice(0, 10);
    const languages = languageAlternates(href);
    lines.push("  <url>");
    lines.push(`    <loc>${xmlEscape(loc)}</loc>`);
    if (lastmod) {
      lines.push(`    <lastmod>${xmlEscape(lastmod)}</lastmod>`);
    }
    for (const [lang, url] of Object.entries(languages)) {
      lines.push(
        `    <xhtml:link rel="alternate" hreflang="${xmlEscape(lang)}" href="${xmlEscape(url)}" />`,
      );
    }
    lines.push("  </url>");
  }
  lines.push("</urlset>");
  return new Response(`${lines.join("\n")}\n`, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
