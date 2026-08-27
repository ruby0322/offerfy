"use client";

import AdminShell from "@/components/admin/AdminShell";
import { ApiError, adminOverview, getMe } from "@/lib/api";
import { useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { notFound } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

type Props = {
  children: ReactNode;
};

export default function AdminGate({ children }: Props) {
  const t = useTranslations("admin");
  const router = useRouter();
  const [status, setStatus] = useState<"loading" | "ok" | "missing">("loading");
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await adminOverview();
        const me = await getMe();
        if (cancelled) return;
        setEmail(me?.email ?? null);
        setStatus("ok");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login?next=/admin");
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setStatus("missing");
          return;
        }
        setStatus("missing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (status === "missing") {
    notFound();
  }

  if (status !== "ok") {
    return <p className="p-8 text-sm text-muted-foreground">{t("loading")}</p>;
  }

  return <AdminShell email={email}>{children}</AdminShell>;
}
