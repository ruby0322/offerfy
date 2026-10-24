import type { MetadataRoute } from "next";
import { routing, type AppLocale } from "@/i18n/routing";
import { SITE_URL } from "@/lib/seo";

const DISALLOW_PATHS = [
  "/admin",
  "/dashboard",
  "/editor",
  "/login",
  "/api",
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
      disallow: [...DISALLOW_PATHS, ...DISALLOW_PATHS.flatMap(withLocales)],
    },
    sitemap: [`${SITE_URL}/sitemap.xml`, `${SITE_URL}/jobs-sitemap.xml`],
  };
}
