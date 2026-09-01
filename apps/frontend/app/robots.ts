import type { MetadataRoute } from "next";
import { routing, type AppLocale } from "@/i18n/routing";
import { SITE_URL } from "@/lib/seo";

const DISALLOW_PATHS = ["/admin", "/dashboard", "/editor", "/login", "/api"] as const;

function withLocales(path: string): string[] {
  return routing.locales.map((locale: AppLocale) => `/${locale}${path}`);
}

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: DISALLOW_PATHS.flatMap(withLocales),
    },
    sitemap: [`${SITE_URL}/sitemap.xml`, `${SITE_URL}/jobs-sitemap.xml`],
  };
}
