"use client";

import { useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/navigation";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";

type Props = {
  variant?: "landing" | "app";
};

export default function Footer({ variant }: Props) {
  const t = useTranslations("footer");
  const pathname = usePathname();
  const isLanding = variant ?? (pathname === "/" ? "landing" : "app");
  const landing = isLanding === "landing";

  return (
    <footer className={landing ? "landing-footer mt-16" : "rr-footer mt-16"}>
      <div
        className={
          landing
            ? "mx-auto flex max-w-[72rem] flex-wrap items-center justify-between gap-4 px-4 py-8 sm:px-5"
            : "mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-8 sm:px-5"
        }
      >
        <p className="text-sm opacity-80">{t("tagline")}</p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Link href="/jobs" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("jobs")}
          </Link>
          <Link href="/blog" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("blog")}
          </Link>
          <Link href="/about" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("about")}
          </Link>
          <Link href="/contact" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("contact")}
          </Link>
          <a
            href="https://x.com/offerfy"
            className="inline-flex min-h-9 items-center text-sm font-medium"
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("x")}
          </a>
          <Link href="/terms" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("terms")}
          </Link>
          <Link href="/privacy" className="inline-flex min-h-9 items-center text-sm font-medium">
            {t("privacy")}
          </Link>
          <LocaleSwitcher />
          <ThemeSwitcher />
          <Link href="/login" className="inline-flex min-h-9 items-center text-sm font-medium">
            {landing ? t("login") : t("google")}
          </Link>
        </div>
      </div>
    </footer>
  );
}
