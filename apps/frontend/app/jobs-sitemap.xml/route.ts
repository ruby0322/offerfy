import { fetchJobSitemapPage } from "@/lib/jobs";
import { SITE_URL } from "@/lib/seo";

function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export async function GET() {
  const first = await fetchJobSitemapPage(0);
  const total = first?.total_pages ?? 0;
  const pages = total > 0 ? total : 0;
  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
  ];
  for (let page = 0; page < pages; page += 1) {
    body.push("  <sitemap>");
    body.push(`    <loc>${xmlEscape(`${SITE_URL}/jobs-sitemap/${page}.xml`)}</loc>`);
    body.push("  </sitemap>");
  }
  body.push("</sitemapindex>");
  return new Response(`${body.join("\n")}\n`, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
