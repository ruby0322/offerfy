"use client";

import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { Button } from "@/components/ui/button";

type Props = {
  brand: string;
  title: string;
  familyMenu?: ReactNode;
  actions?: ReactNode;
  /** The avatar and account menu, last in the row as in the site nav. */
  accountMenu?: ReactNode;
  status?: string;
  publishLabel?: string;
  publishingLabel?: string;
  unpublishedChanges?: boolean;
  publishing?: boolean;
  onPublish?: () => void;
};

export default function EditorHeader({
  brand,
  title,
  familyMenu,
  actions,
  accountMenu,
  status,
  publishLabel,
  publishingLabel,
  unpublishedChanges,
  publishing,
  onPublish,
}: Props) {
  return (
    <header className="border-b border-border bg-background">
      <div className="flex min-w-0 items-center justify-between gap-3 px-3 py-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-3 sm:gap-6">
          <Link
            href="/"
            className="shrink-0 text-lg font-bold tracking-tight text-foreground sm:text-xl"
          >
            {brand}
          </Link>
          {familyMenu ?? <span className="truncate text-sm text-muted-foreground">{title}</span>}
          {status ? (
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{status}</span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-2">
          {actions}
          {onPublish && unpublishedChanges ? (
            <Button
              type="button"
              size="sm"
              disabled={publishing}
              onClick={onPublish}
            >
              {publishing ? publishingLabel : publishLabel}
            </Button>
          ) : null}
          <LocaleSwitcher />
          <ThemeSwitcher />
          {/* Same trailing position as the site nav: the editor has its own
              header and never renders Nav, so without this the account menu is
              missing from the page people spend the most time on. */}
          {accountMenu}
        </div>
      </div>
    </header>
  );
}
