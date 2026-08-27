"use client";

import { adminUsers, type AdminUserListItem } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const LIMIT = 50;

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminUsersPage() {
  const t = useTranslations("admin");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    adminUsers({ q: q.trim() || undefined, limit: LIMIT, offset })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [q, offset]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("users")}</h1>
      <Input
        value={q}
        onChange={(event) => {
          setOffset(0);
          setQ(event.target.value);
        }}
        placeholder={t("search")}
        className="max-w-sm"
      />
      {error ? <p className="text-sm text-destructive">{t("error")}</p> : null}
      {loading ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
      {!loading && items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : null}
      {items.length > 0 ? (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-2 font-medium">{t("email")}</th>
              <th className="py-2 font-medium">{t("locale")}</th>
              <th className="py-2 font-medium">{t("resumeCount")}</th>
              <th className="py-2 font-medium">{t("created")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.id} className="border-b border-border/70">
                <td className="py-2">
                  <Link href={`/admin/users/${row.id}`} className="underline">
                    {row.email}
                  </Link>
                </td>
                <td className="py-2">{row.locale}</td>
                <td className="py-2">{row.resume_count}</td>
                <td className="py-2">{stamp(row.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="flex gap-2 text-sm">
        <button
          type="button"
          className="underline disabled:opacity-40"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - LIMIT))}
        >
          {t("prev")}
        </button>
        <button
          type="button"
          className="underline disabled:opacity-40"
          disabled={offset + LIMIT >= total}
          onClick={() => setOffset(offset + LIMIT)}
        >
          {t("next")}
        </button>
      </div>
    </div>
  );
}
