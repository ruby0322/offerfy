import type { MetadataRoute } from "next";
import { routing, type AppLocale } from "@/i18n/routing";
import { SITE_URL } from "@/lib/seo";

const DISALLOW_PATHS = [
  "/admin",
  "/dashboard",
  "/editor",
  "/login",
  "/api",
  // Both are one signed-in person's own page, so there is nothing here for a
  // crawler to index -- /plan also sets robots: noindex in its own metadata,
  // which is the half that holds if someone links to it.
  "/plan",
  "/settings",
  "/create",
  "/upload",
  "/new",
  "/agent-markdown",
] as const;

function withLocales(path: string): string[] {
  return routing.locales.map((locale: AppLocale) => `/${locale}${path}`);
}

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // The bare paths as well as the locale-prefixed ones. /api/* is not
      // locale-prefixed -- the middleware excludes it -- so listing only
      // /en/api left the real thing crawlable, including the OpenAPI
      // document the backend serves at /api/openapi.json.
      disallow: [
        ...DISALLOW_PATHS,
        ...DISALLOW_PATHS.flatMap(withLocales),
        // Every URL carrying a query string. The boards accept free
        // combinations of company/country/function/seniority/city/remote, so
        // the crawlable filter space is combinatorial: GPTBot and
        // PerplexityBot between them walked 13,269 *distinct* board URLs in 30
        // minutes -- nearly one per request, so no cache can ever hit -- at
        // ~10 backend calls each, which is what took the API down on
        // 2026-11-15.
        //
        // Safe because nothing we want indexed carries a query string:
        // sitemap.xml lists 113 static and board URLs, jobs-sitemap.xml shards
        // ~23k postings, and all of them are clean paths. The bare boards
        // (/jobs, /jobs/internships) and every posting stay crawlable; only
        // the filtered permutations are refused.
        "/*?",
      ],
    },
    sitemap: [`${SITE_URL}/sitemap.xml`, `${SITE_URL}/jobs-sitemap.xml`],
  };
}
