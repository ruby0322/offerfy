"use client";

import { adminUser, type AdminUserDetail } from "@/lib/api";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { use, useEffect, useState } from "react";

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminUserDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const t = useTranslations("admin");
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminUser(id)
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{data.email}</h1>
      <p className="text-sm text-muted-foreground">
        {t("locale")} {data.locale}
        {" · "}
        {t("googleSub")} {data.google_sub}
        {" · "}
        {t("created")} {stamp(data.created_at)}
      </p>
      {data.resumes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-2 font-medium">{t("title")}</th>
              <th className="py-2 font-medium">{t("source")}</th>
              <th className="py-2 font-medium">{t("importStatus")}</th>
              <th className="py-2 font-medium">{t("messages")}</th>
              <th className="py-2 font-medium">{t("created")}</th>
            </tr>
          </thead>
          <tbody>
            {data.resumes.map((row) => (
              <tr key={row.id} className="border-b border-border/70">
                <td className="py-2">
                  <Link href={`/admin/resumes/${row.id}`} className="underline">
                    {row.title}
                  </Link>
                </td>
                <td className="py-2">{row.source}</td>
                <td className="py-2">{row.import_status}</td>
                <td className="py-2">{row.message_count}</td>
                <td className="py-2">{stamp(row.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
