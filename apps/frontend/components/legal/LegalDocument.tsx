import { getTranslations } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JsonLd from "@/components/seo/JsonLd";
import { Link } from "@/i18n/navigation";
import { organizationJsonLd } from "@/lib/seo";

export const LEGAL_UPDATED = "2026-09-01";

type LegalDoc = "terms" | "privacy";

type LegalSection = {
  heading: string;
  paragraphs: string[];
  rows?: string[][];
};

type Props = {
  doc: LegalDoc;
};

export default async function LegalDocument({ doc }: Props) {
  const t = await getTranslations(`legal.${doc}`);
  const common = await getTranslations("legal");
  const sections = t.raw("sections") as LegalSection[];
  const otherHref = doc === "terms" ? "/privacy" : "/terms";
  const seeAlso = doc === "terms" ? "seeAlsoPrivacy" : "seeAlsoTerms";

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <article className="legal-article">
          <h1 className="font-display">{t("title")}</h1>
          <p className="legal-updated">{common("lastUpdated", { date: LEGAL_UPDATED })}</p>
          <p>{t("intro")}</p>
          {sections.map((section) => (
            <section key={section.heading}>
              <h2 className="font-display">{section.heading}</h2>
              {section.paragraphs.map((paragraph, index) => (
                <p key={`${section.heading}-${index}`}>{paragraph}</p>
              ))}
              {section.rows && section.rows.length > 0 ? (
                <div className="legal-table-wrap">
                  <table className="legal-table">
                    <thead>
                      <tr>
                        <th scope="col">{common("cookieName")}</th>
                        <th scope="col">{common("cookiePurpose")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {section.rows.map((row) => (
                        <tr key={row[0]}>
                          <td>
                            <code>{row[0]}</code>
                          </td>
                          <td>{row[1]}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>
          ))}
          <p className="legal-see-also">
            <Link href={otherHref}>{common(seeAlso)}</Link>
          </p>
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}
