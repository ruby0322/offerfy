import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { Link } from "@/i18n/navigation";
import { getTranslations } from "next-intl/server";
import { SITE_URL } from "@/lib/seo";

export default async function NotFoundRecovery() {
  const t = await getTranslations("notFound");
  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <main>
        <article className="legal-article">
          <h1 className="font-display">{t("title")}</h1>
          <p>{t("lead")}</p>
          <ul className="not-found-links">
            <li>
              <Link href="/">{t("home")}</Link>
            </li>
            <li>
              <a href="/llms.txt">{t("llms")}</a>
            </li>
            <li>
              <a href={`${SITE_URL}/sitemap.xml`}>{t("sitemap")}</a>
            </li>
            <li>
              <Link href="/jobs">{t("jobs")}</Link>
            </li>
            <li>
              <Link href="/blog">{t("blog")}</Link>
            </li>
          </ul>
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}
