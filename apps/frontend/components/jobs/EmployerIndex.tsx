import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import EmployerLogo from "@/components/jobs/EmployerLogo";
import { EMPLOYERS_HREF } from "@/lib/employers";
import type { JobEmployer } from "@/lib/jobs";

type Props = {
  employers: JobEmployer[];
  total: number;
};

/**
 * Every employer with something open, biggest first.
 *
 * Unpaginated, matching the endpoint: this is ~80 rows, and the whole point
 * of the page is that a crawler (and a visitor) can see all of them at once.
 * If the board list ever grows by an order of magnitude, both ends need a
 * cursor -- see GET /v1/jobs/employers.
 *
 * Biggest first rather than alphabetical because the first screen should
 * answer "who is actually hiring", and the names are scannable either way.
 */
export default async function EmployerIndex({ employers, total }: Props) {
  const t = await getTranslations("jobs");

  return (
    <div className="jobs-index mx-auto max-w-[72rem] px-5">
      <p className="jobs-crumb">
        <Link href="/jobs">{t("title")}</Link>
        <span aria-hidden="true"> › </span>
        <span>{t("employersTitle")}</span>
      </p>
      <h1 className="font-display">{t("employersTitle")}</h1>
      <p className="jobs-lead">{t("employersLead")}</p>

      {employers.length === 0 ? (
        <p className="blog-empty">{t("emptyCatalog")}</p>
      ) : (
        <>
          <p className="jobs-count">{t("employersCount", { count: total })}</p>
          <ul className="jobs-employer-grid">
            {employers.map((employer) => (
              <li key={employer.slug}>
                <Link href={`${EMPLOYERS_HREF}/${employer.slug}`} className="jobs-employer-card">
                  <EmployerLogo company={employer.company} />
                  <span className="jobs-employer-card-text">
                    <span className="jobs-employer-card-name">{employer.company}</span>
                    {/* The same two phrases the pulse leaderboard uses, so a
                        card and a leaderboard row describing one employer
                        read the same way. */}
                    <span className="jobs-employer-card-meta">
                      {t("employerOpenCount", { count: employer.open_count })}
                      {employer.new_count > 0
                        ? ` · ${t("employerNewCount", { count: employer.new_count })}`
                        : null}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
