"use client";

import { Link } from "@/i18n/navigation";
import LocaleSwitcher from "@/components/LocaleSwitcher";
import ThemeSwitcher from "@/components/ThemeSwitcher";

type Props = {
  brand: string;
  title: string;
  status?: string;
};

export default function EditorHeader({ brand, title, status }: Props) {
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
          <span className="truncate text-sm text-muted-foreground">{title}</span>
          {status ? (
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{status}</span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-2">
          <LocaleSwitcher />
          <ThemeSwitcher />
        </div>
      </div>
    </header>
  );
}
