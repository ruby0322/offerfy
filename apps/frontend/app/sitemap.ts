import type { MetadataRoute } from "next";
import { listPosts } from "@/lib/blog/load";
import { languageAlternates, pageUrl } from "@/lib/seo";
import { routing } from "@/i18n/routing";

function entry(href: string, lastModified: Date): MetadataRoute.Sitemap[number] {
  const languages = languageAlternates(href);
  return {
    url: pageUrl(href, routing.defaultLocale),
    lastModified,
    alternates: { languages },
  };
}

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  const posts = listPosts();
  const staticPaths = ["/", "/terms", "/privacy", "/blog", "/jobs"];
  const items = staticPaths.map((href) => entry(href, now));
  for (const post of posts) {
    const lastModified = new Date(`${post.updatedAt ?? post.publishedAt}T00:00:00Z`);
    items.push(entry(`/blog/${post.slug}`, lastModified));
  }
  return items;
}
