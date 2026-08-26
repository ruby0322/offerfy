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
    <header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      <div className="flex min-w-0 items-center justify-between gap-3 px-3 py-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-3 sm:gap-6">
          <Link
            href="/"
            className="shrink-0 text-lg font-bold tracking-tight text-gray-900 sm:text-xl dark:text-white"
          >
            {brand}
          </Link>
          <span className="truncate text-sm text-gray-500 dark:text-gray-400">{title}</span>
          {status ? (
            <span className="hidden shrink-0 text-xs text-gray-400 sm:inline">{status}</span>
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
