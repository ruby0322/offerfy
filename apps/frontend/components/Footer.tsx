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
    <footer className={landing ? "landing-footer mt-16" : "rr-footer mt-16 border-t"}>
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-8">
        <p className="text-sm opacity-80">{t("tagline")}</p>
        <div className="flex items-center gap-1 sm:gap-4">
          <LocaleSwitcher />
          <ThemeSwitcher />
          <Link href="/login" className="text-sm font-medium">
            {t("google")}
          </Link>
        </div>
      </div>
    </footer>
  );
}
