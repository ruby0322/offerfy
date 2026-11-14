"use client";

import { useLocale, useTranslations } from "next-intl";
import { useIsClient } from "@/lib/use-is-client";

/**
 * Both of these say something about *now*, in the reader's own timezone, so
 * neither can be rendered on the server: "2 hours ago" and "today" are both
 * wrong by the time the HTML is cached, and the server's UTC idea of "today"
 * is not the reader's. The pattern is therefore the same in both — a stable,
 * timezone-free fallback for the server pass and the hydration pass, swapped
 * for the real thing once useIsClient flips — with suppressHydrationWarning
 * because that swap is the point rather than a mismatch to fix.
 *
 * The fallback is never empty: a row that renders nothing where the age goes
 * and then pops text in is worse than one that says "13 Nov" and sharpens.
 */

function absolute(iso: string, locale: string): string {
  // UTC-pinned, so the server and the client agree before the swap.
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(iso));
}

export function relativeLabel(iso: string, locale: string, now: Date): string {
  const then = new Date(iso);
  const seconds = (then.getTime() - now.getTime()) / 1000;
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "narrow" });
  const minutes = seconds / 60;
  if (Math.abs(minutes) < 60) {
    // Rounded toward zero, so a posting 59 seconds old reads "0 min. ago"
    // rather than claiming a minute that has not passed.
    return format.format(Math.trunc(minutes), "minute");
  }
  const hours = minutes / 60;
  if (Math.abs(hours) < 24) return format.format(Math.trunc(hours), "hour");
  const days = hours / 24;
  if (Math.abs(days) < 30) return format.format(Math.trunc(days), "day");
  return absolute(iso, locale);
}

/** How old a posting is, e.g. "41 min. ago", "yesterday", "13 Nov". */
export function JobTime({ iso }: { iso: string }) {
  const locale = useLocale();
  const isClient = useIsClient();
  const text = isClient ? relativeLabel(iso, locale, new Date()) : absolute(iso, locale);
  return (
    <time className="job-when" dateTime={iso} suppressHydrationWarning>
      {text}
    </time>
  );
}

/**
 * When ingest last confirmed anything, as a clock time the reader can check
 * against their own clock: "catalog last refreshed at 14:32 today". A
 * duration ("2h ago") reads as precision the six-hourly ingest does not have,
 * and goes stale in the page cache; a wall-clock time does neither.
 */
export function RefreshedAt({ iso }: { iso: string | null }) {
  const locale = useLocale();
  const t = useTranslations("jobs");
  const isClient = useIsClient();

  let text: string;
  if (!iso) {
    text = t("refreshedUnknown");
  } else if (!isClient) {
    text = t("refreshedOn", { date: absolute(iso, locale) });
  } else {
    const then = new Date(iso);
    const now = new Date();
    const time = new Intl.DateTimeFormat(locale, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(then);
    // Calendar days in the reader's zone, not elapsed hours: 00:30 is
    // "yesterday" at 01:00 even though it is half an hour ago.
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfThen = new Date(then.getFullYear(), then.getMonth(), then.getDate());
    const daysBack = Math.round((startOfToday.getTime() - startOfThen.getTime()) / 86400000);
    if (daysBack <= 0) text = t("refreshedToday", { time });
    else if (daysBack === 1) text = t("refreshedYesterday", { time });
    else
      text = t("refreshedOn", {
        date: new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(then),
      });
  }

  return (
    <span className="jobs-pulse-age" suppressHydrationWarning>
      {text}
    </span>
  );
}
