import { useTranslations } from "next-intl";
import { wallRows, type WallItem } from "@/components/landing/companies";
import { EMPLOYER_COUNT } from "@/lib/seo";

const ROWS = 3;
const ROW_CLASS = ["landing-rail-a", "landing-rail-b", "landing-rail-c"];

function Chip({ item }: { item: WallItem }) {
  return (
    <span className="landing-chip">
      {item.d ? (
        <svg viewBox="0 0 24 24" aria-hidden="true" fill={item.hex ?? "var(--ink)"}>
          <path d={item.d} />
        </svg>
      ) : null}
      <span className={item.d ? "landing-chip-name" : "landing-chip-word"}>{item.name}</span>
    </span>
  );
}

export default function CompanyWall() {
  const t = useTranslations("landing.companies");
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      {/* The rail is decoration over the note below it, which states the same
          fact in text; a screen reader gets that instead of 72 duplicated chips. */}
      <div className="landing-rail" aria-hidden="true">
        {wallRows(ROWS).map((row, index) => (
          <div key={index} className={`landing-rail-row ${ROW_CLASS[index % ROWS]}`}>
            {[...row, ...row].map((item, i) => (
              <Chip key={`${item.name}-${i}`} item={item} />
            ))}
          </div>
        ))}
      </div>
      <p className="landing-section-note">{t("note", { count: EMPLOYER_COUNT })}</p>
    </section>
  );
}
