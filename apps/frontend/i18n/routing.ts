import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["en", "zh-TW", "zh-CN"],
  defaultLocale: "zh-TW",
  localePrefix: "as-needed",
  localeDetection: false,
  localeCookie: {
    name: "offerfy_locale",
  },
});

export type AppLocale = (typeof routing.locales)[number];
