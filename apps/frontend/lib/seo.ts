import type { Metadata } from "next";
import { getPathname } from "@/i18n/navigation";
import { routing, type AppLocale } from "@/i18n/routing";

export const SITE_URL = "https://offerfy.cc";
export const SITE_NAME = "Offerfy";
export const CONTACT_EMAIL = "james@offerfy.cc";
export const ORG_DESCRIPTION =
  "The AI resume editor you’ll keep using. Chat edits this file. No account needed.";

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
    description: ORG_DESCRIPTION,
    contactPoint: {
      "@type": "ContactPoint",
      contactType: "customer support",
      email: CONTACT_EMAIL,
    },
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

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function jobDescriptionHtml(html: string, text: string, title: string): string {
  const trimmedHtml = html.trim();
  if (trimmedHtml) return trimmedHtml;
  const body = (text.trim() || title).trim();
  return `<p>${escapeHtml(body)}</p>`;
}

/**
 * How long after we last confirmed a posting exists we are still willing to
 * vouch for it. Keep in step with JOB_FRESHNESS_DAYS in
 * apps/backend/app/routers/jobs.py, which uses it to drop stale rows from the
 * jobs sitemap.
 */
export const JOB_FRESHNESS_DAYS = 30;

const FRESHNESS_MS = JOB_FRESHNESS_DAYS * 24 * 60 * 60 * 1000;

function validThroughIso(lastSeenAt: string): string {
  const stamp = new Date(lastSeenAt);
  stamp.setUTCDate(stamp.getUTCDate() + JOB_FRESHNESS_DAYS);
  return stamp.toISOString();
}

/**
 * Whether a posting may still carry JobPosting markup and stay indexable.
 *
 * `is_active` on its own is not enough. It only flips when an ingest run sees
 * the posting gone from its source, so a stalled ingest leaves every row
 * active while its validThrough slides into the past — which is precisely the
 * expired-posting markup Google drops from Google Jobs. Gating on last_seen_at
 * means a dead ingest degrades to noindex rather than to 11k lies.
 */
export function isJobIndexable(lastSeenAt: string, now: Date = new Date()): boolean {
  const seen = new Date(lastSeenAt);
  if (Number.isNaN(seen.getTime())) return false;
  return seen.getTime() + FRESHNESS_MS > now.getTime();
}

export function jobPostingJsonLd(input: {
  locale: AppLocale;
  href: string;
  jobId: string;
  title: string;
  descriptionHtml: string;
  descriptionText: string;
  company: string;
  location: string | null;
  remote: boolean | null;
  datePosted: string | null;
  lastSeenAt: string;
}): Record<string, unknown> | null {
  const remote = input.remote === true;
  const location = input.location?.trim() || "";
  if (!remote && !location) {
    return null;
  }

  const posting: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: input.title,
    description: jobDescriptionHtml(input.descriptionHtml, input.descriptionText, input.title),
    url: pageUrl(input.href, input.locale),
    identifier: {
      "@type": "PropertyValue",
      name: SITE_NAME,
      value: input.jobId,
    },
    hiringOrganization: {
      "@type": "Organization",
      name: input.company,
    },
    directApply: false,
    validThrough: validThroughIso(input.lastSeenAt),
  };
  if (input.datePosted) {
    posting.datePosted = input.datePosted;
  }
  if (remote) {
    posting.jobLocationType = "TELECOMMUTE";
  }
  if (location) {
    posting.jobLocation = {
      "@type": "Place",
      address: {
        "@type": "PostalAddress",
        addressLocality: location,
      },
    };
  }
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
