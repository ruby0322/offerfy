import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { wallRows, type WallItem } from "@/components/landing/companies";
import { EMPLOYERS_HREF, employerHref, employerSlug } from "@/lib/employers";
import { fetchJobEmployerCount, fetchJobEmployerSlugs } from "@/lib/jobs";
import { EMPLOYER_COUNT } from "@/lib/seo";

const ROWS = 3;
const ROW_CLASS = ["landing-rail-a", "landing-rail-b", "landing-rail-c"];

/**
 * Where a chip goes: that employer's own page.
 *
 * With one fallback, and it is not cosmetic. The wall is a static list of
 * names, but an employer page is a 404 when that employer has nothing open
 * right now -- the backend refuses to serve an empty one -- and which
 * employers those are changes with every ingest pass. So a chip only points
 * at the page when the live set says that page exists, and otherwise at the
 * filtered catalogue, which renders whatever is there, including nothing.
 * Today that is one chip of 91 (Eventbrite); tomorrow it is a different one.
 *
 * `pages` empty means the catalog could not be reached, not that no employer
 * has a page, so every chip falls back rather than every chip 404ing.
 */
function chipHref(name: string, pages: Set<string>): string {
  const page = employerHref(name);
  return page && pages.has(employerSlug(name))
    ? page
    : `/jobs?company=${encodeURIComponent(name)}`;
}

function Chip({
  item,
  dup,
  href,
  label,
}: {
  item: WallItem;
  dup: boolean;
  href: string;
  label: string;
}) {
  return (
    <Link
      href={href}
      className={`landing-chip${dup ? " landing-chip-dup" : ""}`}
      // The second copy of the row exists only so the loop has no seam. It is
      // the same link twice, so it is hidden from assistive tech and taken out
      // of the tab order -- otherwise tabbing the wall visits all 90 employers
      // twice. Same pattern as the jobs marquee.
      tabIndex={dup ? -1 : undefined}
      aria-hidden={dup || undefined}
      title={label}
    >
      {item.d ? (
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          fill={item.hex ?? "var(--ink)"}
          fillRule={item.fillRule}
        >
          <path d={item.d} />
        </svg>
      ) : null}
      <span className={item.d ? "landing-chip-name" : "landing-chip-word"}>{item.name}</span>
    </Link>
  );
}

export default async function CompanyWall() {
  const t = await getTranslations("landing.companies");
  // The number of employers with something open, which is what the page this
  // caption links to counts. EMPLOYER_COUNT is the fallback and means a
  // different thing -- how many boards we ingest, which includes employers
  // with nothing open right now -- so the two are close but not the same, and
  // the live one is the honest answer to "how many employers are here".
  // One request behind both: the count comes off `total`, the set off
  // `items`, and backendJsonCached dedupes them on the same path and TTL.
  const [carried, pages] = await Promise.all([
    fetchJobEmployerCount(),
    fetchJobEmployerSlugs(),
  ]);
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      {/* No longer aria-hidden: every chip is a real link to that employer's
          page, and hiding interactive content from assistive tech while
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
                href={chipHref(item.name, pages)}
                label={t("chipLabel", { company: item.name })}
              />
            ))}
          </div>
        ))}
      </div>
      <p className="landing-section-note">
        {t("note", { count: carried ?? EMPLOYER_COUNT })}
      </p>
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
