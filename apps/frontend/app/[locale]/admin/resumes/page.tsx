"use client";

import { adminResumes, type AdminResumeListItem } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

const LIMIT = 50;

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminResumesPage() {
  const t = useTranslations("admin");
  const [q, setQ] = useState("");
  const [owner, setOwner] = useState("");
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AdminResumeListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    adminResumes({
      q: q.trim() || undefined,
      owner: owner || undefined,
      source: source || undefined,
      limit: LIMIT,
      offset,
    })
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
  }, [q, owner, source, offset]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("resumes")}</h1>
      <div className="flex flex-wrap gap-3">
        <Input
          value={q}
          onChange={(event) => {
            setOffset(0);
            setQ(event.target.value);
          }}
          placeholder={t("search")}
          className="max-w-sm"
        />
        <select
          className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
          value={owner}
          onChange={(event) => {
            setOffset(0);
            setOwner(event.target.value);
          }}
        >
          <option value="">{t("all")}</option>
          <option value="user">{t("ownerUser")}</option>
          <option value="guest">{t("ownerGuest")}</option>
        </select>
        <select
          className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
          value={source}
          onChange={(event) => {
            setOffset(0);
            setSource(event.target.value);
          }}
        >
          <option value="">{t("all")}</option>
          <option value="create">{t("sourceCreate")}</option>
          <option value="upload">{t("sourceUpload")}</option>
        </select>
      </div>
      {error ? <p className="text-sm text-destructive">{t("error")}</p> : null}
      {loading ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
      {!loading && items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : null}
      {items.length > 0 ? (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-2 font-medium">{t("title")}</th>
              <th className="py-2 font-medium">{t("owner")}</th>
              <th className="py-2 font-medium">{t("source")}</th>
              <th className="py-2 font-medium">{t("importStatus")}</th>
              <th className="py-2 font-medium">{t("messages")}</th>
              <th className="py-2 font-medium">{t("created")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.id} className="border-b border-border/70">
                <td className="py-2">
                  <Link href={`/admin/resumes/${row.id}`} className="underline">
                    {row.title}
                  </Link>
                </td>
                <td className="py-2">
                  {row.owner_kind === "user" ? (
                    <Link href={`/admin/users/${row.owner_id}`} className="underline">
                      {row.owner_label}
                    </Link>
                  ) : (
                    row.owner_label
                  )}
                </td>
                <td className="py-2">{row.source}</td>
                <td className="py-2">{row.import_status}</td>
                <td className="py-2">{row.message_count}</td>
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
