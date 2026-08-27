"use client";

import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import GoogleMark from "@/components/GoogleMark";
import Nav from "@/components/Nav";
import { Link } from "@/i18n/navigation";
import { googleStartUrl } from "@/lib/api";

export default function LoginPage() {
  const t = useTranslations("login");
  const tLegal = useTranslations("legal");

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="rr-auth-main">
        <div className="rr-card w-full max-w-[26rem] px-8 py-10">
          <h1 className="rr-page-title">{t("title")}</h1>
          <p className="rr-page-lead">{t("lead")}</p>
          <a className="rr-btn rr-btn-google mt-8 w-full py-3" href={googleStartUrl()}>
            <GoogleMark />
            {t("google")}
          </a>
          <p className="legal-agree">
            {tLegal.rich("loginAgree", {
              terms: (chunks) => <Link href="/terms">{chunks}</Link>,
              privacy: (chunks) => <Link href="/privacy">{chunks}</Link>,
            })}
          </p>
          <Link href="/" className="rr-back-link mt-8 inline-block text-sm">
            ← {t("back")}
          </Link>
        </div>
      </main>
      <Footer variant="app" />
    </div>
  );
}
