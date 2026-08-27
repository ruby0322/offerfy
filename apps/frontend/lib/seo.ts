import type { Metadata } from "next";
import { getPathname } from "@/i18n/navigation";
import { routing, type AppLocale } from "@/i18n/routing";

export const SITE_URL = "https://offerfy.cc";
export const SITE_NAME = "Offerfy";

export const OG_LOCALE: Record<AppLocale, string> = {
  en: "en_US",
  "zh-TW": "zh_TW",
  "zh-CN": "zh_CN",
};

export function pageUrl(href: string, locale: AppLocale): string {
  const pathname = getPathname({ href, locale });
  return `${SITE_URL}${pathname}`;
}

export function languageAlternates(href: string): Record<string, string> {
  const languages: Record<string, string> = {
    "x-default": pageUrl(href, routing.defaultLocale),
  };
  for (const locale of routing.locales) {
    languages[locale] = pageUrl(href, locale);
  }
  return languages;
}

type PageMetaInput = {
  locale: AppLocale;
  href: string;
  title: string;
  description: string;
  type?: "website" | "article";
  publishedTime?: string;
  modifiedTime?: string;
};

export function pageMetadata({
  locale,
  href,
  title,
  description,
  type = "website",
  publishedTime,
  modifiedTime,
}: PageMetaInput): Metadata {
  const canonical = pageUrl(href, locale);
  const languages = languageAlternates(href);
  const alternateLocale = routing.locales
    .filter((item) => item !== locale)
    .map((item) => OG_LOCALE[item]);

  return {
    title,
    description,
    alternates: {
      canonical,
      languages,
    },
    openGraph: {
      type,
      url: canonical,
      siteName: SITE_NAME,
      title,
      description,
      locale: OG_LOCALE[locale],
      alternateLocale,
      ...(type === "article"
        ? {
            publishedTime,
            modifiedTime,
          }
        : {}),
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}

function organizationNode() {
  return {
    "@type": "Organization",
    name: SITE_NAME,
    url: SITE_URL,
  };
}

export function organizationJsonLd() {
  return {
    "@context": "https://schema.org",
    ...organizationNode(),
  };
}

export function blogJsonLd(locale: AppLocale) {
  return {
    "@context": "https://schema.org",
    "@type": "Blog",
    name: `${SITE_NAME} Blog`,
    url: pageUrl("/blog", locale),
    publisher: organizationNode(),
  };
}

export function blogPostingJsonLd(input: {
  locale: AppLocale;
  href: string;
  title: string;
  description: string;
  publishedAt: string;
  updatedAt?: string;
}) {
  return {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: input.title,
    description: input.description,
    datePublished: input.publishedAt,
    dateModified: input.updatedAt ?? input.publishedAt,
    inLanguage: input.locale,
    mainEntityOfPage: pageUrl(input.href, input.locale),
    publisher: organizationNode(),
  };
}
