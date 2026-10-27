"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import NavUsage from "@/components/NavUsage";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { logout, type AuthUser } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  /** `undefined` while the auth check is in flight, `null` once it says nobody. */
  user: AuthUser | null | undefined;
  /** Called after the session is gone, for whatever state the page kept about it. */
  onLoggedOut?: () => void;
};

function initials(user: AuthUser): string {
  // Their name before their email: once someone has set a display name, an
  // avatar still showing the first letter of their address is wrong about who
  // they are. Falls through to the email, which every account has.
  const name = (user.display_name || "").trim();
  if (name) return name[0]!.toUpperCase();
  const email = (user.email || "").trim();
  if (email) return email[0]!.toUpperCase();
  return "?";
}

/**
 * The avatar and the account menu behind it.
 *
 * It lives here rather than inside Nav because the editor has its own header
 * and does not render Nav at all, which left the one control that is supposed
 * to be on every page missing from the page people spend the most time on. One
 * component means the menu cannot drift between the two headers.
 *
 * The caller owns the user: both headers already ask `getMe()` for their own
 * reasons, so taking it as a prop keeps this from being a second request for an
 * answer the page has. Signed out it renders nothing -- what to offer a guest
 * instead is the header's business, and the two headers answer differently.
 */
export default function AccountMenu({ user, onLoggedOut }: Props) {
  const t = useTranslations("nav");

  async function onLogout() {
    try {
      await logout();
    } finally {
      onLoggedOut?.();
    }
  }

  // While the answer is still in flight, hold the avatar's space rather than
  // let the rest of the header slide sideways when it lands.
  if (user === undefined) return <span className="inline-block size-8" aria-hidden="true" />;
  if (!user) return null;

  return (
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
      {/* 14rem, the same cap `.nav-usage` puts on the meter, so the
          menu is already the width the meter needs and does not widen
          when the usage request lands. */}
      <DropdownMenuContent align="end" className="min-w-56">
        {user.email ? (
          <>
            {/* The name they chose on top, with the handle and the
                address under it. The email stays visible because it is
                which account this is; the name is only who they are. */}
            <DropdownMenuLabel className="max-w-52 font-normal">
              {user.display_name ? (
                <span className="block truncate font-medium">{user.display_name}</span>
              ) : null}
              {user.username ? (
                <span className="block truncate text-muted-foreground">@{user.username}</span>
              ) : null}
              <span className="block truncate text-muted-foreground">{user.email}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        ) : null}
        {/* Straight under the identity row: who this account is, then
            what it has left this week. It brings its own trailing
            separator, so the actions below stay their own group
            whether or not the meter is there. */}
        <NavUsage />
        {/* Above Settings and not inside NavUsage's block: that block
            renders only once its fetch lands and only where limits are
            enforced, and the route to this page must not depend on
            either. */}
        <DropdownMenuItem asChild>
          <Link href="/plan">{t("planAndUsage")}</Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/settings">{t("settings")}</Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void onLogout()}>{t("logout")}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
