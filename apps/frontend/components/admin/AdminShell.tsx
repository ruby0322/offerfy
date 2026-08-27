"use client";

import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

type Props = {
  email: string | null;
  children: ReactNode;
};

export default function AdminShell({ email, children }: Props) {
  const t = useTranslations("admin");
  const pathname = usePathname();

  const links = [
    { href: "/admin", label: t("overview"), exact: true },
    { href: "/admin/users", label: t("users"), exact: false },
    { href: "/admin/resumes", label: t("resumes"), exact: false },
  ] as const;

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="px-4 py-5 text-sm font-semibold tracking-wide">{t("ops")}</div>
        <nav className="flex flex-1 flex-col gap-1 px-2">
          {links.map((item) => {
            const active = item.exact
              ? pathname === item.href
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-md px-3 py-2 text-sm",
                  active ? "bg-[#2d3a5a]" : "opacity-70 hover:opacity-100",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex flex-col gap-2 border-t border-white/10 px-3 py-4 text-xs">
          {email ? <p className="truncate opacity-80">{email}</p> : null}
          <div className="flex items-center gap-1">
            <LocaleSwitcher />
            <ThemeSwitcher />
          </div>
          <Link href="/dashboard" className="opacity-70 hover:opacity-100">
            {t("dashboard")}
          </Link>
        </div>
      </aside>
      <div className="admin-main">
        <div className="mx-auto max-w-6xl px-6 py-8">{children}</div>
      </div>
    </div>
  );
}
