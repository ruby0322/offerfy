"use client";

import { adminOverview, type AdminOverview } from "@/lib/api";
import AdminCharts from "@/components/admin/AdminCharts";
import { Link } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

function stamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminOverviewPage() {
  const t = useTranslations("admin");
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminOverview()
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-destructive">{t("error")}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;

  const cards = [
    [t("usersCount"), data.counts.users],
    [t("guestsCount"), data.counts.guest_sessions],
    [t("resumesCount"), data.counts.resumes],
    [t("chats24h"), data.counts.chat_messages_24h],
    [t("chats7d"), data.counts.chat_messages_7d],
  ] as const;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">{t("overview")}</h1>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {cards.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-border bg-sheet p-4">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-2xl font-semibold">{value}</p>
          </div>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        {t("healthApi")} {data.health.api === "ok" ? t("ok") : t("unavailable")}
        {" · "}
        {t("healthDb")} {data.health.database === "ok" ? t("ok") : t("unavailable")}
        {" · "}
        {t("healthS3")} {data.health.s3_configured ? t("configured") : t("notConfigured")}
        {" · "}
        {t("rateChat")} {data.counts.guest_rate_chat_24h}
        {" · "}
        {t("rateExport")} {data.counts.guest_rate_export_24h}
      </p>
      <AdminCharts series={data.series ?? []} counts={data.counts} />
      <section>
        <h2 className="mb-3 text-lg font-medium">{t("recentUsers")}</h2>
        {data.recent_users.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        ) : (
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
              {data.recent_users.map((row) => (
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
        )}
      </section>
      <section>
        <h2 className="mb-3 text-lg font-medium">{t("recentResumes")}</h2>
        {data.recent_resumes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="py-2 font-medium">{t("title")}</th>
                <th className="py-2 font-medium">{t("owner")}</th>
                <th className="py-2 font-medium">{t("source")}</th>
                <th className="py-2 font-medium">{t("created")}</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_resumes.map((row) => (
                <tr key={row.id} className="border-b border-border/70">
                  <td className="py-2">
                    <Link href={`/admin/resumes/${row.id}`} className="underline">
                      {row.title}
                    </Link>
                  </td>
                  <td className="py-2">{row.owner_label}</td>
                  <td className="py-2">{row.source}</td>
                  <td className="py-2">{stamp(row.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
