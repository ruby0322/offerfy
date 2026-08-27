import { getTranslations, setRequestLocale } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { Link } from "@/i18n/navigation";
import { resolveLocale } from "@/lib/locale";

type Props = {
  params: Promise<{ locale: string }>;
};

export default async function NewPickerPage({ params }: Props) {
  const { locale: localeParam } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  const t = await getTranslations("picker");

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-3xl px-5 py-16">
        <h1 className="rr-page-title">{t("title")}</h1>
        <p className="rr-page-lead">{t("lead")}</p>
        <div className="mt-10 grid gap-4 md:grid-cols-2">
          <Link href="/create" className="rr-card rr-choice-card block p-7">
            <h2 className="text-lg font-semibold tracking-tight">{t("createTitle")}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{t("createBody")}</p>
          </Link>
          <Link href="/upload" className="rr-card rr-choice-card block p-7">
            <h2 className="text-lg font-semibold tracking-tight">{t("uploadTitle")}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{t("uploadBody")}</p>
          </Link>
        </div>
      </main>
      <Footer variant="app" />
    </div>
  );
}
