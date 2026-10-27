"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import AccountMenu from "@/components/AccountMenu";
import BrandMark from "@/components/BrandMark";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { getMe, type AuthUser } from "@/lib/api";

type Props = {
  variant?: "landing" | "app";
};

export default function Nav({ variant }: Props) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const router = useRouter();
  const isLanding = variant ?? (pathname === "/" ? "landing" : "app");
  const landing = isLanding === "landing";
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((next) => {
        if (!cancelled) setUser(next);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <header className={landing ? "landing-nav" : "rr-nav"}>
      <div
        className={
          landing
            ? "mx-auto flex max-w-[72rem] items-center justify-between gap-2 px-4 py-2 sm:gap-4 sm:px-5 sm:py-3"
            : "mx-auto flex max-w-6xl items-center justify-between gap-2 px-4 py-2 sm:gap-4 sm:px-5 sm:py-3"
        }
      >
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <Link
            href="/"
            className="flex min-w-0 shrink-0 items-center gap-2 text-lg font-semibold tracking-tight"
          >
            <BrandMark />
            {t("brand")}
          </Link>
          {landing ? (
            <>
              <Link href="/jobs" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
                {t("jobs")}
              </Link>
              <Link href="/blog" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
                {t("blog")}
              </Link>
              <Link href="/pricing" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
                {t("pricing")}
              </Link>
            </>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-3">
          {user ? (
            <>
              <Link href="/dashboard" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
                {t("dashboard")}
              </Link>
              {user.is_admin ? (
                <Link href="/admin" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
                  {t("admin")}
                </Link>
              ) : null}
            </>
          ) : null}
          <LocaleSwitcher />
          <ThemeSwitcher />
          <AccountMenu
            user={user}
            onLoggedOut={() => {
              setUser(null);
              router.push("/");
            }}
          />
          {user === null ? (
            <Link href="/login" className="inline-flex min-h-9 items-center rounded-md px-1 text-sm font-medium">
              {t("login")}
            </Link>
          ) : null}
        </div>
      </div>
    </header>
  );
}
