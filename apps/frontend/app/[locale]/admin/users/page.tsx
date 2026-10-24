"use client";

import { adminUsers, type AdminUserListItem } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const LIMIT = 50;

function tierKey(tier: string) {
  if (tier === "guest") return "usageTier_guest" as const;
  if (tier === "matching") return "usageTier_matching" as const;
  return "usageTier_registered" as const;
}

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminUsersPage() {
  const t = useTranslations("admin");
  const [q, setQ] = useState("");
  const [order, setOrder] = useState<"created" | "usage">("created");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    adminUsers({ q: q.trim() || undefined, order, limit: LIMIT, offset })
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
  }, [q, order, offset]);

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
      {/* The whole point of the usage columns: one click answers "who is
          costing the most this week" without opening anyone's page. */}
      <div className="flex gap-2 text-sm">
        {(["created", "usage"] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setOffset(0);
              setOrder(key);
            }}
            className={
              order === key
                ? "rounded-md border border-border px-2 py-1 font-medium"
                : "rounded-md border border-transparent px-2 py-1 text-muted-foreground"
            }
          >
            {t(key === "created" ? "orderCreated" : "orderUsage")}
          </button>
        ))}
      </div>
      {error ? <p className="text-sm text-destructive">{t("error")}</p> : null}
      {loading ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
      {!loading && items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : null}
      {items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[48rem] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="py-2 font-medium">{t("email")}</th>
                <th className="py-2 font-medium">{t("locale")}</th>
                <th className="py-2 font-medium">{t("resumeCount")}</th>
                <th className="py-2 font-medium">{t("usageColTier")}</th>
                <th className="py-2 text-right font-medium">{t("usageColCredits")}</th>
                <th className="py-2 text-right font-medium">{t("usageColTurns")}</th>
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
                  <td className="py-2">{t(tierKey(row.tier))}</td>
                  <td className="py-2 text-right">{row.credits_used}</td>
                  <td className="py-2 text-right">{row.turns_used}</td>
                  <td className="py-2">{stamp(row.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
