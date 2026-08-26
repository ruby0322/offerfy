"use client";

import { useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/navigation";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";

type Props = {
  variant?: "landing" | "app";
};

export default function Nav({ variant }: Props) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const isLanding = variant ?? (pathname === "/" ? "landing" : "app");
  const landing = isLanding === "landing";

  return (
    <header className={landing ? "landing-nav" : "rr-nav border-b"}>
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          {t("brand")}
        </Link>
        <div className="flex items-center gap-1 sm:gap-3">
          <LocaleSwitcher />
          <ThemeSwitcher />
          <Link href="/login" className={landing ? "text-sm font-medium" : "rr-btn-nav"}>
            {landing ? t("google") : t("login")}
          </Link>
        </div>
      </div>
    </header>
  );
}
