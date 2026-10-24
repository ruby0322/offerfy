import { getTranslations } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JsonLd from "@/components/seo/JsonLd";
import { Link } from "@/i18n/navigation";
import { organizationJsonLd } from "@/lib/seo";
import { fetchUsageTiers } from "@/lib/usage-tiers";

type Section = { heading: string; paragraphs: string[] };

/**
 * The public explanation of the AI usage limits.
 *
 * The section order is the argument and should not be rearranged: what is free
 * everywhere comes first, because the limit is on one feature rather than on
 * the product, and leading with the limit misrepresents what Offerfy is. The
 * matching paragraph comes last and says plainly that the bonus is a
 * thank-you — that is the public half of the consent constraint in the design
 * spec, not decoration.
 *
 * The tier numbers come from the API, never from the message files. See
 * lib/usage-tiers.ts.
 */
export default async function UsageDocument() {
  const t = await getTranslations("usage");
  const common = await getTranslations("legal");
  const tiers = await fetchUsageTiers();

  const free = t.raw("freeItems") as string[];
  const costlier = t.raw("costlierItems") as string[];
  const sections = t.raw("sections") as Section[];

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <article className="legal-article">
          <h1 className="font-display">{t("title")}</h1>
          <p>{t("intro")}</p>

          {/* First, and deliberately: the limit is on one feature, not on the
              product. Every line here is true because nothing in the compile,
              preview, export or ATS path calls a model. */}
          <section>
            <h2 className="font-display">{t("freeHeading")}</h2>
            <p>{t("freeIntro")}</p>
            <ul className="usage-list">
              {free.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="font-display">{t("whyHeading")}</h2>
            <p>{t("whyBody")}</p>
          </section>

          <section>
            <h2 className="font-display">{t("tiersHeading")}</h2>
            {tiers ? (
              <div className="legal-table-wrap">
                <table className="legal-table">
                  <thead>
                    <tr>
                      <th scope="col">{t("colTier")}</th>
                      <th scope="col">{t("colAllowance")}</th>
                      <th scope="col">{t("colPublish")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tiers.tiers.map((row) => (
                      <tr key={row.name}>
                        <th scope="row">{t(`tier_${row.name}`)}</th>
                        <td>
                          {row.turns !== null
                            ? t("allowanceEdits", { count: row.turns })
                            : t("allowanceCredits", { edits: row.approx_edits })}
                        </td>
                        <td>{row.can_publish ? t("yes") : t("no")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              /* The table is the one thing on this page we will not guess at.
                 If the API is unreachable, say so rather than print a number
                 that might not be the enforced one. */
              <p>{t("tiersUnavailable")}</p>
            )}
            <p>{t("tiersNote")}</p>
          </section>

          <section>
            <h2 className="font-display">{t("costlierHeading")}</h2>
            <p>{t("costlierIntro")}</p>
            <ul className="usage-list">
              {costlier.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          {sections.map((section) => (
            <section key={section.heading}>
              <h2 className="font-display">{section.heading}</h2>
              {section.paragraphs.map((paragraph, index) => (
                <p key={`${section.heading}-${index}`}>{paragraph}</p>
              ))}
            </section>
          ))}

          <p className="legal-see-also">
            <Link href="/terms">{common("seeAlsoTerms")}</Link>
          </p>
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}
