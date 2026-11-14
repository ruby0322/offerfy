"use client";

import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import AccountMenu from "@/components/AccountMenu";
import BrandMark from "@/components/BrandMark";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { EMPLOYERS_HREF } from "@/lib/employers";
import { getMe, type AuthUser } from "@/lib/api";

type Props = {
  variant?: "landing" | "app";
};

// One link, in the two places it can be. `display` is deliberately NOT in
// the shared part: "hidden md:inline-flex" and a bare "inline-flex" in the
// same class list are two utilities for one property, and the one that wins
// is whichever Tailwind emits last -- which is `inline-flex`, so the links
// never hid at all.
const LINK = "min-h-9 items-center rounded-md px-1 text-sm font-medium";
/** For a link that is its own hide/show unit. */
const WIDE_ONLY = `hidden md:inline-flex ${LINK}`;
/** For a link inside a container that is already hidden on a phone. */
const WIDE_INNER = `inline-flex ${LINK}`;
const DRAWER_LINK =
  "flex min-h-11 items-center rounded-md px-2 text-[0.95rem] font-medium";

export default function Nav({ variant }: Props) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const router = useRouter();
  const isLanding = variant ?? (pathname === "/" ? "landing" : "app");
  const landing = isLanding === "landing";
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  const [open, setOpen] = useState(false);

  // A link inside the drawer navigates without unmounting the header, so
  // nothing else would close it; the path changing is exactly the moment it
  // should go. Adjusted during render rather than in an effect (the same
  // shape JobResults uses for `initial`), which also covers the back button.
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(false);
  }

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

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Rendered twice, once per row, because the two rows style them
  // differently and a phone sees only one of the two.
  const signedInLinks = (className: string) =>
    user ? (
      <>
        <Link href="/dashboard" className={className}>
          {t("dashboard")}
        </Link>
        {user.is_admin ? (
          <Link href="/admin" className={className}>
            {t("admin")}
          </Link>
        ) : null}
      </>
    ) : null;

  // The landing header's own links. An app page has none of them: its
  // header carries the app's controls, and the brand goes home.
  const sections: [string, string][] = landing
    ? [
        ["/jobs", t("jobs")],
        [EMPLOYERS_HREF, t("employers")],
        ["/blog", t("blog")],
        ["/pricing", t("pricing")],
      ]
    : [];

  return (
    <header className={landing ? "landing-nav" : "rr-nav"}>
      <div
        className={
          landing
            ? "nav-bar mx-auto flex max-w-[72rem] items-center justify-between gap-2 px-4 py-2 sm:gap-4 sm:px-5 sm:py-3"
            : "nav-bar mx-auto flex max-w-6xl items-center justify-between gap-2 px-4 py-2 sm:gap-4 sm:px-5 sm:py-3"
        }
      >
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <Link
            href="/"
            className="flex min-w-0 shrink-0 items-center gap-2 text-base font-semibold tracking-tight sm:text-lg"
          >
            <BrandMark />
            {t("brand")}
          </Link>
          {/* Below md every one of these lives in the drawer instead: four
              links plus the brand plus the account cluster overflow a phone
              header, and a header that overflows is a header whose last
              link cannot be tapped. */}
          {sections.map(([href, text]) => (
            <Link key={href} href={href} className={WIDE_ONLY}>
              {text}
            </Link>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-3">
          <div className="hidden items-center gap-1 sm:gap-3 md:flex">
            {signedInLinks(WIDE_INNER)}
            <LocaleSwitcher />
            <ThemeSwitcher />
          </div>
          {/* The avatar stays on the row at every width: it is one tap wide,
              and it is the control people reach for most. */}
          <AccountMenu
            user={user}
            onLoggedOut={() => {
              setUser(null);
              router.push("/");
            }}
          />
          {user === null ? (
            <Link href="/login" className={WIDE_ONLY}>
              {t("login")}
            </Link>
          ) : null}
          <button
            type="button"
            className="nav-burger inline-flex md:hidden"
            aria-label={t("menu")}
            aria-expanded={open}
            aria-controls="nav-drawer"
            onClick={() => setOpen((was) => !was)}
          >
            {open ? (
              <X aria-hidden="true" className="size-5" />
            ) : (
              <Menu aria-hidden="true" className="size-5" />
            )}
          </button>
        </div>
      </div>

      {/* Hangs off the header rather than sitting in it: the app header is a
          fixed 3.5rem (.rr-shell .rr-nav), so a drawer in the flow would be
          cut off by it. */}
      {open ? (
        <>
          <button
            type="button"
            className="nav-drawer-scrim md:hidden"
            aria-label={t("menu")}
            tabIndex={-1}
            onClick={() => setOpen(false)}
          />
          <nav id="nav-drawer" className="nav-drawer flex flex-col md:hidden">
            {sections.map(([href, text]) => (
              <Link key={href} href={href} className={DRAWER_LINK}>
                {text}
              </Link>
            ))}
            {signedInLinks(DRAWER_LINK)}
            {user === null ? (
              <Link href="/login" className={DRAWER_LINK}>
                {t("login")}
              </Link>
            ) : null}
            <div className="nav-drawer-tools">
              <LocaleSwitcher />
              <ThemeSwitcher />
            </div>
          </nav>
        </>
      ) : null}
    </header>
  );
}
