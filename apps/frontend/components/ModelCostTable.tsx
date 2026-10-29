import { useTranslations } from "next-intl";
import type { ModelRow, ModelTable } from "@/lib/usage-tiers";

/**
 * "Each model spends your allowance at a different rate", as a table.
 *
 * Purely presentational, and shared by three pages that fetch the same rows
 * from three different places: /usage and /pricing on the server (they are
 * indexable, so a crawler must see the numbers in the HTML) and /plan in the
 * browser. One component because the claim it makes is a published figure, and
 * three copies of a published figure is three chances for one to drift.
 *
 * The columns answer the only question a reader actually has. It is not "what
 * is the per-token price" -- nobody knows how many tokens their resume is, and
 * the API deliberately publishes no rate. It is "if I pick this one, how much
 * of my week does it use up", which is the edits column, with the relative
 * figure beside it to make the ordering legible at a glance.
 *
 * It is a sync component so it renders in both a Server and a Client
 * Component; `useTranslations` is what makes that possible, and it is why this
 * file is not marked "use client" despite the hook.
 */

type Tier = "guest" | "registered" | "matching";

type Props = {
  table: ModelTable;
  /** Whose allowance the edits column is quoted against. */
  tier: Tier;
  /** Marks one row as the reader's own current choice. Omitted on /usage and
   *  /pricing, which have no reader to be specific about. */
  selected?: string | null;
};

/**
 * Which tier's edit count to show.
 *
 * A guest has no cost-capped allowance of their own -- their cap counts turns,
 * so the figure is the same whichever model runs. They are shown what an
 * account would get, which is the honest answer to the question a guest
 * looking at this table is actually asking.
 */
function quotedTier(tier: Tier): "registered" | "matching" {
  return tier === "matching" ? "matching" : "registered";
}

export function modelEdits(row: ModelRow, tier: Tier): number | null {
  const value = row.approx_edits[quotedTier(tier)];
  return typeof value === "number" ? value : null;
}

/**
 * next-intl types its message keys, so a template literal is not one. These
 * also mean a tier the backend grows without copy here fails the build rather
 * than rendering a raw key -- the same reason PricingPage spells its keys out.
 */
function editsColumnKey(tier: "registered" | "matching") {
  return tier === "matching" ? ("colEdits_matching" as const) : ("colEdits_registered" as const);
}

function noteKey(tier: "registered" | "matching") {
  return tier === "matching"
    ? ("tableNote_matching" as const)
    : ("tableNote_registered" as const);
}

function unlockKey(minTier: Tier) {
  if (minTier === "matching") return "unlockedBy_matching" as const;
  if (minTier === "registered") return "unlockedBy_registered" as const;
  return "unlockedBy_guest" as const;
}

export default function ModelCostTable({ table, tier, selected }: Props) {
  const t = useTranslations("models");
  const quoted = quotedTier(tier);
  // Only when at least one row carries unconfirmed prices, so the footnote
  // disappears once they are all verified rather than becoming furniture.
  const provisional = table.models.some((row) => row.prices_provisional);

  return (
    <>
      <div className="legal-table-wrap">
        <table className="legal-table">
          <thead>
            <tr>
              <th scope="col">{t("colModel")}</th>
              <th scope="col">{t("colCost")}</th>
              <th scope="col">{t(editsColumnKey(quoted))}</th>
              <th scope="col">{t("colAvailable")}</th>
            </tr>
          </thead>
          <tbody>
            {table.models.map((row) => {
              const edits = modelEdits(row, tier);
              const tags = [
                row.id === selected ? t("yours") : null,
                row.id === table.reference ? t("isDefault") : null,
              ].filter(Boolean);
              return (
                <tr key={row.id}>
                  <th scope="row">
                    {row.label}
                    {tags.length > 0 ? ` · ${tags.join(" · ")}` : ""}
                  </th>
                  {/* The relative figure, not a price. "1x" is the default by
                      definition, which is what makes the column readable. */}
                  <td>{t("costTimes", { weight: row.cost_weight })}</td>
                  <td>{edits === null ? "—" : t("editsAbout", { count: edits })}</td>
                  <td>
                    {row.available ? t("availableYes") : t(unlockKey(row.min_tier))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* Said once, under the table, rather than repeated per row: the
          allowance is one number and the models divide it differently.

          `model-note` rather than a bare <p>: on /usage these sit inside
          .legal-article, which spaces its own paragraphs, but on /plan they
          sit in a Card that has no prose rules at all and they would run
          together. The class matches the article's spacing so the two pages
          read the same. */}
      <p className="model-note">{t(noteKey(quoted))}</p>
      {tier === "guest" ? <p className="model-note">{t("guestNote")}</p> : null}
      {provisional ? <p className="model-note">{t("provisionalNote")}</p> : null}
    </>
  );
}
