"use client";

import type { ReactNode } from "react";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";
import { Button } from "@/components/ui/button";

type Props = {
  /** The way out of the editor, where the brand mark used to be. */
  back: ReactNode;
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
  back,
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
          {/* The brand mark used to sit here and was the only way out of the
              editor, pointing at the marketing front page -- which is almost
              never where someone leaving a resume wants to land. A labelled
              exit is worth more here than the logo. */}
          {back}
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
