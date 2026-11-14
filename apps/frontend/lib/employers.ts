/**
 * The employer page's URL, derived from the company name.
 *
 * A posting on the wire carries `company` and no slug -- an employer has no
 * id anywhere in this system, the name *is* the identity (see
 * services/jobs/employers.py on the backend, and components/employer-marks.ts,
 * which joins on the same string). So a job row that wants to link to the
 * employer has to derive the slug itself, and this must fold exactly the way
 * the backend folds or the link 404s. employers.test.ts pins the pairs that
 * matter; the backend's test_jobs_employers.py pins the same ones.
 *
 * The one case this cannot get right: two employers whose names differ only
 * in case or punctuation both fold to one slug, and the backend refuses to
 * guess between them, so the link lands on a 404. That is deliberate -- a
 * wrong employer's job list would be worse -- and no two boards collide
 * today. If one is ever added, the fix is on the board's spelling.
 */
export function employerSlug(company: string): string {
  return company
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Where an employer's page lives, or null when the name makes no slug. */
export function employerHref(company: string): string | null {
  const slug = employerSlug(company);
  return slug ? `/jobs/employers/${slug}` : null;
}

export const EMPLOYERS_HREF = "/jobs/employers";
