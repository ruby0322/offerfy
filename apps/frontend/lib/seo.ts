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

export function jobPostingJsonLd(input: {
  locale: AppLocale;
  href: string;
  title: string;
  description: string;
  company: string;
  location: string | null;
  remote: boolean | null;
  datePosted: string | null;
  applyUrl: string;
}) {
  const posting: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: input.title,
    description: input.description,
    url: pageUrl(input.href, input.locale),
    hiringOrganization: {
      "@type": "Organization",
      name: input.company,
    },
    directApply: false,
  };
  if (input.datePosted) {
    posting.datePosted = input.datePosted;
  }
  if (input.remote) {
    posting.jobLocationType = "TELECOMMUTE";
  }
  if (input.location) {
    posting.jobLocation = {
      "@type": "Place",
      address: input.location,
    };
  }
  posting.url = pageUrl(input.href, input.locale);
  return posting;
}

export function shareOgImageUrl(token: string): string {
  return `${SITE_URL}/api/v1/shares/${encodeURIComponent(token)}/og.png`;
}

export async function publicShareExists(token: string): Promise<boolean> {
  const base = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
  try {
    const res = await fetch(`${base}/v1/shares/${encodeURIComponent(token)}`, {
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}
