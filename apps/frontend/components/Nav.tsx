"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import BrandMark from "@/components/BrandMark";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getMe, logout, type AuthUser } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  variant?: "landing" | "app";
};

function initials(user: AuthUser): string {
  const email = (user.email || "").trim();
  if (email) return email[0]!.toUpperCase();
  return "?";
}

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

  async function onLogout() {
    try {
      await logout();
    } finally {
      setUser(null);
      router.push("/");
    }
  }

  return (
    <header className={landing ? "landing-nav" : "rr-nav"}>
      <div
        className={
          landing
            ? "mx-auto flex max-w-[72rem] items-center justify-between gap-4 px-5 py-3"
            : "mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3"
        }
      >
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-lg font-semibold tracking-tight"
          >
            <BrandMark />
            {t("brand")}
          </Link>
          {landing ? (
            <>
              <Link href="/jobs" className="text-sm font-medium">
                {t("jobs")}
              </Link>
              <Link href="/blog" className="text-sm font-medium">
                {t("blog")}
              </Link>
            </>
          ) : null}
        </div>
        <div className="flex items-center gap-1 sm:gap-3">
          {user ? (
            <Link href="/dashboard" className="text-sm font-medium">
              {t("dashboard")}
            </Link>
          ) : null}
          <LocaleSwitcher />
          <ThemeSwitcher />
          {user === undefined ? (
            <span className="inline-block size-8" aria-hidden="true" />
          ) : user ? (
            <DropdownMenu>
              <DropdownMenuTrigger
                className={cn(
                  "inline-flex size-8 items-center justify-center rounded-full p-0",
                  "outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
                aria-label={t("account")}
              >
                <Avatar className="size-8">
                  {user.picture ? (
                    <AvatarImage src={user.picture} alt="" referrerPolicy="no-referrer" />
                  ) : null}
                  <AvatarFallback className="text-xs font-medium">{initials(user)}</AvatarFallback>
                </Avatar>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-44">
                {user.email ? (
                  <>
                    <DropdownMenuLabel className="max-w-52 truncate font-normal text-muted-foreground">
                      {user.email}
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                  </>
                ) : null}
                <DropdownMenuItem onSelect={() => void onLogout()}>{t("logout")}</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Link href="/login" className="text-sm font-medium">
              {t("login")}
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
