import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { wallRows, type WallItem } from "@/components/landing/companies";
import { EMPLOYERS_HREF } from "@/lib/employers";
import { EMPLOYER_COUNT } from "@/lib/seo";

const ROWS = 3;
const ROW_CLASS = ["landing-rail-a", "landing-rail-b", "landing-rail-c"];

function Chip({ item, dup, label }: { item: WallItem; dup: boolean; label: string }) {
  return (
    <Link
      href={`/jobs?company=${encodeURIComponent(item.name)}`}
      className={`landing-chip${dup ? " landing-chip-dup" : ""}`}
      // The second copy of the row exists only so the loop has no seam. It is
      // the same link twice, so it is hidden from assistive tech and taken out
      // of the tab order -- otherwise tabbing the wall visits all 88 employers
      // twice. Same pattern as the jobs marquee.
      tabIndex={dup ? -1 : undefined}
      aria-hidden={dup || undefined}
      title={label}
    >
      {item.d ? (
        <svg viewBox="0 0 24 24" aria-hidden="true" fill={item.hex ?? "var(--ink)"}>
          <path d={item.d} />
        </svg>
      ) : null}
      <span className={item.d ? "landing-chip-name" : "landing-chip-word"}>{item.name}</span>
    </Link>
  );
}

export default function CompanyWall() {
  const t = useTranslations("landing.companies");
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      {/* No longer aria-hidden: every chip is a real link to that employer's
          roles, and hiding interactive content from assistive tech while
          leaving it clickable is the worst of both. The note below still
          states the same fact in prose for anyone who does not want to walk
          88 links. */}
      <div className="landing-rail">
        {wallRows(ROWS).map((row, index) => (
          <div key={index} className={`landing-rail-row ${ROW_CLASS[index % ROWS]}`}>
            {[...row, ...row].map((item, i) => (
              <Chip
                key={`${item.name}-${i}`}
                item={item}
                dup={i >= row.length}
                label={t("chipLabel", { company: item.name })}
              />
            ))}
          </div>
        ))}
      </div>
      <p className="landing-section-note">{t("note", { count: EMPLOYER_COUNT })}</p>
      {/* The wall is a sample on a loop; this is the whole list, with what
          each employer has open right now. */}
      <p className="landing-section-note">
        <Link href={EMPLOYERS_HREF} className="landing-cta-link">
          {t("allEmployers")}
        </Link>
      </p>
    </section>
  );
}
