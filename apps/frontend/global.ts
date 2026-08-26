import { routing } from "./i18n/routing";
import zhTW from "./messages/zh-TW.json";

declare module "next-intl" {
  interface AppConfig {
    Locale: (typeof routing.locales)[number];
    Messages: typeof zhTW;
  }
}
