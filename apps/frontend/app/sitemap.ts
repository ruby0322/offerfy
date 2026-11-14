import type { MetadataRoute } from "next";
import { listPosts } from "@/lib/blog/load";
import { EMPLOYERS_HREF } from "@/lib/employers";
import { JOB_BOARDS } from "@/lib/job-boards";
import { fetchJobEmployers } from "@/lib/jobs";
import { languageAlternates, pageUrl } from "@/lib/seo";
import { routing } from "@/i18n/routing";

/**
 * Rendered per request, not at build.
 *
 * The employer pages below come from the live catalog (one URL per employer
 * with something open), so a build-time snapshot would be a list of whoever
 * was hiring the day we deployed -- and, if the backend were not reachable
 * during the build, a sitemap silently missing every employer until the next
 * deploy. The file is small and a crawler asks for it rarely.
 */
export const dynamic = "force-dynamic";

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

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const posts = listPosts();
  const staticPaths = ["/", "/terms", "/privacy", "/usage", "/pricing", "/blog", "/jobs", "/about", "/contact"];
  const items = staticPaths.map((href) => entry(href));
  // The curated boards. They are the reason those pages exist rather than
  // query strings: a sitemap cannot carry /jobs?seniority=intern.
  for (const board of JOB_BOARDS) {
    items.push(entry(`/jobs/${board.slug}`));
  }
  // One page per employer with a live posting. Nothing is prerendered for
  // these, and an employer who stops hiring 404s -- so the list is fetched
  // here rather than derived from the board file, which would keep naming
  // employers whose postings have all closed.
  items.push(entry(EMPLOYERS_HREF));
  const { items: employers } = await fetchJobEmployers();
  for (const employer of employers) {
    items.push(entry(`${EMPLOYERS_HREF}/${employer.slug}`));
  }
  for (const post of posts) {
    const lastModified = new Date(`${post.updatedAt ?? post.publishedAt}T00:00:00Z`);
    items.push(entry(`/blog/${post.slug}`, lastModified));
  }
  return items;
}
