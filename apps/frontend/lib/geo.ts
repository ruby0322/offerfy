/**
 * Guessing which country a visitor is in, well enough to preselect a filter.
 *
 * The browser's own geolocation API is not used, deliberately: it throws a
 * permission dialog at a first-time visitor who only wanted to see some jobs,
 * and it answers to street level when the question is "which of 18 countries".
 * The IANA timezone the device already reports answers that question for free,
 * silently, offline, and correctly for anyone who has their clock set right —
 * which is almost everyone, because a wrong clock breaks everything else too.
 *
 * Only the countries the catalogue actually carries are listed. A timezone
 * that is not here (Asia/Bangkok, say) resolves to nothing, and nothing is
 * the right answer: a filter preset to a country with no roles would be worse
 * than no preset at all.
 */

const ZONE_COUNTRY: Record<string, string> = {
  "Asia/Taipei": "TW",
  "Asia/Shanghai": "CN",
  "Asia/Chongqing": "CN",
  "Asia/Harbin": "CN",
  "Asia/Urumqi": "CN",
  "Asia/Macau": "CN",
  "Asia/Hong_Kong": "HK",
  "Asia/Tokyo": "JP",
  "Asia/Seoul": "KR",
  "Asia/Singapore": "SG",
  "Asia/Kolkata": "IN",
  "Asia/Calcutta": "IN",
  "Asia/Jerusalem": "IL",
  "Asia/Tel_Aviv": "IL",
  "Europe/London": "GB",
  "Europe/Belfast": "GB",
  "Europe/Dublin": "IE",
  "Europe/Berlin": "DE",
  "Europe/Busingen": "DE",
  "Europe/Paris": "FR",
  "Europe/Amsterdam": "NL",
  "Europe/Zurich": "CH",
  "Europe/Warsaw": "PL",
  "America/Toronto": "CA",
  "America/Montreal": "CA",
  "America/Vancouver": "CA",
  "America/Edmonton": "CA",
  "America/Winnipeg": "CA",
  "America/Halifax": "CA",
  "America/St_Johns": "CA",
  "America/Regina": "CA",
  "America/New_York": "US",
  "America/Detroit": "US",
  "America/Chicago": "US",
  "America/Denver": "US",
  "America/Phoenix": "US",
  "America/Los_Angeles": "US",
  "America/Anchorage": "US",
  "America/Boise": "US",
  "America/Juneau": "US",
  "Pacific/Honolulu": "US",
  "Australia/Sydney": "AU",
  "Australia/Melbourne": "AU",
  "Australia/Brisbane": "AU",
  "Australia/Perth": "AU",
  "Australia/Adelaide": "AU",
};

/** Prefixes for the zones an exact match misses (America/Indiana/* etc.). */
const ZONE_PREFIX: [string, string][] = [
  ["America/Indiana/", "US"],
  ["America/Kentucky/", "US"],
  ["America/North_Dakota/", "US"],
  ["Australia/", "AU"],
];

/** Last resort: what the page is being read in. Not a location, but a hint. */
const LOCALE_COUNTRY: Record<string, string> = {
  "zh-TW": "TW",
  "zh-CN": "CN",
  en: "US",
};

export function countryFromTimeZone(zone: string | undefined | null): string | null {
  if (!zone) return null;
  const exact = ZONE_COUNTRY[zone];
  if (exact) return exact;
  for (const [prefix, country] of ZONE_PREFIX) {
    if (zone.startsWith(prefix)) return country;
  }
  return null;
}

export function countryFromLocale(locale: string | undefined | null): string | null {
  if (!locale) return null;
  return LOCALE_COUNTRY[locale] ?? null;
}

/**
 * The visitor's country, by clock first and language second, or null when
 * neither says anything this catalogue can use.
 *
 * `available` is the set of countries the catalogue currently has roles in:
 * a guess that is right about the person but useless here (they are in
 * Thailand, we carry nothing there) must not become a filter.
 */
export function detectCountry(locale: string, available: Set<string>): string | null {
  let zone: string | undefined;
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    zone = undefined;
  }
  const byZone = countryFromTimeZone(zone);
  if (byZone && available.has(byZone)) return byZone;
  const byLocale = countryFromLocale(locale);
  if (byLocale && available.has(byLocale)) return byLocale;
  return null;
}

/** What the picker shows as the reason, e.g. "Asia/Taipei". */
export function currentTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}
