import { getTranslations } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JsonLd from "@/components/seo/JsonLd";
import { organizationJsonLd } from "@/lib/seo";

type Kind = "about" | "contact";

export default async function TrustDocument({ kind }: { kind: Kind }) {
  const t = await getTranslations(kind);
  const paragraphs = t.raw("paragraphs") as string[];
  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <article className="legal-article">
          <h1 className="font-display">{t("title")}</h1>
          {paragraphs.map((paragraph) => (
            <p key={paragraph.slice(0, 24)}>{paragraph}</p>
          ))}
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}
