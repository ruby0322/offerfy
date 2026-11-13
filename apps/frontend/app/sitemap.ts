import type { MetadataRoute } from "next";
import { listPosts } from "@/lib/blog/load";
import { JOB_BOARDS } from "@/lib/job-boards";
import { languageAlternates, pageUrl } from "@/lib/seo";
import { routing } from "@/i18n/routing";

function entry(
  href: string,
  lastModified?: Date,
): MetadataRoute.Sitemap[number] {
  const languages = languageAlternates(href);
  return {
    url: pageUrl(href, routing.defaultLocale),
    ...(lastModified ? { lastModified } : {}),
    alternates: { languages },
  };
}

export default function sitemap(): MetadataRoute.Sitemap {
  const posts = listPosts();
  const staticPaths = ["/", "/terms", "/privacy", "/usage", "/pricing", "/blog", "/jobs", "/about", "/contact"];
  const items = staticPaths.map((href) => entry(href));
  // The curated boards. They are the reason those pages exist rather than
  // query strings: a sitemap cannot carry /jobs?seniority=intern.
  for (const board of JOB_BOARDS) {
    items.push(entry(`/jobs/${board.slug}`));
  }
  for (const post of posts) {
    const lastModified = new Date(`${post.updatedAt ?? post.publishedAt}T00:00:00Z`);
    items.push(entry(`/blog/${post.slug}`, lastModified));
  }
  return items;
}
