import AdminGate from "@/components/admin/AdminGate";
import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  const t = await getTranslations({ locale, namespace: "admin" });
  return {
    title: t("metaTitle"),
    robots: { index: false, follow: false },
  };
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  return <AdminGate>{children}</AdminGate>;
}
