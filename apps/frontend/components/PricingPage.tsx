import { getTranslations } from "next-intl/server";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import JsonLd from "@/components/seo/JsonLd";
import { Link } from "@/i18n/navigation";
import { organizationJsonLd } from "@/lib/seo";
import { fetchUsageTiers, type UsageTierRow } from "@/lib/usage-tiers";

/**
 * The pricing page of a product with no prices.
 *
 * It takes the shape readers expect — three plan cards, a figure at the top of
 * each, a feature list — because that is the shape someone comes looking for
 * when they want to know what this costs. The answer is just nothing, and the
 * fastest way to say so is in the format built for saying the opposite.
 *
 * The allowances come from the API, never from the message files, for the same
 * reason /usage does: a published number that has drifted from the enforced one
 * is the worst bug either page could have. See lib/usage-tiers.ts.
 *
 * It does not say "free forever". The Terms are explicit that Phase 1 promises
 * nothing about features remaining unchanged, and a pricing page that makes a
 * permanent commitment the Terms disclaim is worse than no pricing page. The
 * present tense plus an honest line about what happens if it changes is the
 * strongest thing that is also true.
 */
export default async function PricingPage() {
  const t = await getTranslations("pricing");
  const tiers = await fetchUsageTiers();

  const included = t.raw("includedItems") as string[];
  const notes = t.raw("notes") as { heading: string; body: string }[];

  function allowance(row: UsageTierRow): string {
    return row.turns !== null
      ? t("allowanceEdits", { count: row.turns })
      : t("allowanceCredits", { edits: row.approx_edits });
  }

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <article className="legal-article">
          <p className="pricing-kicker">{t("kicker")}</p>
          <h1 className="font-display pricing-title">{t("title")}</h1>
          <p className="pricing-lede">{t("lede")}</p>

          {tiers ? (
            <div className="pricing-grid">
              {tiers.tiers.map((row) => (
                <section key={row.name} className="pricing-card">
                  <h2 className="pricing-card-name font-display">{t(nameKey(row.name))}</h2>
                  {/* The figure a pricing page exists to show. */}
                  <p className="pricing-price">
                    {t("free")}
                    <span className="pricing-price-note">{t("perMonth")}</span>
                  </p>
                  <p className="pricing-card-allowance">{allowance(row)}</p>
                  <ul className="pricing-card-list">
                    <li>{row.can_publish ? t("canPublish") : t("cannotPublish")}</li>
                    <li>{t("burst", { count: row.turns_per_hour })}</li>
                    <li>{t("everythingElse")}</li>
                  </ul>
                  <p className="pricing-card-how">{t(howKey(row.name))}</p>
                </section>
              ))}
            </div>
          ) : (
            /* The numbers are the one thing on this page worth refusing to
               guess at, the same as on /usage. */
            <p>{t("tiersUnavailable")}</p>
          )}

          <section>
            <h2 className="font-display">{t("includedHeading")}</h2>
            <p>{t("includedIntro")}</p>
            <ul className="usage-list">
              {included.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          {notes.map((note) => (
            <section key={note.heading}>
              <h2 className="font-display">{note.heading}</h2>
              <p>{note.body}</p>
            </section>
          ))}

          <p className="legal-see-also">
            <Link href="/usage">{t("seeUsage")}</Link>
          </p>
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}

/**
 * next-intl types its message keys, so a template literal is not one. These
 * also mean a tier the backend grows without copy here fails the build rather
 * than rendering a raw key.
 */
function nameKey(tier: string) {
  if (tier === "guest") return "tier_guest" as const;
  if (tier === "matching") return "tier_matching" as const;
  return "tier_registered" as const;
}

function howKey(tier: string) {
  if (tier === "guest") return "how_guest" as const;
  if (tier === "matching") return "how_matching" as const;
  return "how_registered" as const;
}
