"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { usePathname } from "@/i18n/navigation";

/**
 * The dashboard's six views.
 *
 * Real routes rather than client-side tabs, so each view is bookmarkable, the
 * back button works, and one view's data fetch is not paid for by the other
 * five. The overview is deliberately the short one: it holds what changed and
 * the one next action, and every number that wants studying lives on the tab
 * that owns it.
 */

const TABS = [
  { href: "/dashboard", key: "tabOverview" },
  { href: "/dashboard/applications", key: "tabApplications" },
  { href: "/dashboard/matches", key: "tabMatches" },
  { href: "/dashboard/resumes", key: "tabResumes" },
  { href: "/dashboard/profile", key: "tabProfile" },
  { href: "/dashboard/usage", key: "tabUsage" },
] as const;

export default function DashboardTabs() {
  const t = useTranslations("dashboard");
  const pathname = usePathname();

  return (
    <nav aria-label={t("tabOverview")} className="border-b border-rule bg-sheet">
      {/* Scrolls rather than wraps on a phone: six tabs on two lines reads as
          two rows of navigation instead of one. */}
      <div className="mx-auto flex max-w-5xl gap-1 overflow-x-auto px-4 sm:px-5">
        {TABS.map((tab) => {
          // The overview must match exactly or it would light up on every
          // child route; the others match their own subtree.
          const active =
            tab.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname.startsWith(tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active ? "page" : undefined}
              className={
                active
                  ? "whitespace-nowrap border-b-2 border-primary px-3 py-2.5 text-sm font-semibold text-foreground"
                  : "whitespace-nowrap border-b-2 border-transparent px-3 py-2.5 text-sm font-semibold text-muted-foreground hover:text-foreground"
              }
            >
              {t(tab.key)}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
