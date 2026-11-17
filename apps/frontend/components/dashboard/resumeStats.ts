import type { Application } from "@/lib/applications";
import type { Resume } from "@/lib/api";

/**
 * What a resume has actually done, from the two lists the overview already
 * holds.
 *
 * "Most used" only means something ONE level up. A tailored resume is cut for
 * a single posting, so almost every tailor has been sent exactly once and a
 * master is never linked to an application at all -- ranking the documents
 * themselves tops the list with tied tailors and buries the document the
 * person actually works on. Counted as a FAMILY, the same data answers the
 * question properly: a master's reach is everything its tailors did, and that
 * number separates "the resume I keep cutting versions from" from the one I
 * made once and abandoned.
 *
 * A master's own id is counted alongside its tailors' even though nothing
 * links an application to a master today. It costs one entry in a Set, and it
 * means the figure stays right rather than silently low if that ever changes.
 */

export type ResumeStats = {
  /** Tailored copies cut from this resume. Zero for a tailor. */
  tailors: number;
  sent: number;
  replied: number;
  /**
   * The most recent application in this family that an employer answered,
   * for the one line on the page that names a win. Null when none did --
   * the caller drops the line rather than printing a zero.
   */
  reply: Application | null;
};

function isMaster(resume: Resume): boolean {
  return (resume.kind ?? "master") === "master";
}

function stamp(resume: Resume): number {
  const at = Date.parse(resume.updated_at ?? "");
  return Number.isNaN(at) ? 0 : at;
}

/** The copies cut from this resume; empty unless it is a master. */
export function tailorsOf(resume: Resume, resumes: Resume[]): Resume[] {
  if (!isMaster(resume)) return [];
  return resumes.filter((r) => r.kind === "tailor" && r.parent_resume_id === resume.id);
}

export function statsFor(
  resume: Resume,
  resumes: Resume[],
  applications: Application[],
): ResumeStats {
  const tailors = tailorsOf(resume, resumes);
  const family = new Set([resume.id, ...tailors.map((r) => r.id)]);
  const sent = applications.filter(
    (a) => a.tailor_resume_id !== null && family.has(a.tailor_resume_id),
  );
  const answered = sent
    .filter((a) => a.replied_at !== null)
    .sort((a, b) => Date.parse(b.replied_at ?? "") - Date.parse(a.replied_at ?? ""));
  return {
    tailors: tailors.length,
    sent: sent.length,
    replied: answered.length,
    reply: answered[0] ?? null,
  };
}

/**
 * The resume the overview puts on screen: the master whose family has been
 * sent the most, and the one edited most recently when two are level -- which
 * is every account with a single master, the common case, where this simply
 * picks it.
 *
 * Falls back to the most recently touched document when there is no master at
 * all. That is not a hypothetical: a tailor whose parent was deleted outlives
 * it, and the resumes tab already has a name for those (orphans).
 */
export function featuredResume(resumes: Resume[], applications: Application[]): Resume | null {
  if (resumes.length === 0) return null;
  const masters = resumes.filter(isMaster);
  if (masters.length === 0) {
    return [...resumes].sort((a, b) => stamp(b) - stamp(a))[0];
  }
  return [...masters].sort((a, b) => {
    const reach = statsFor(b, resumes, applications).sent - statsFor(a, resumes, applications).sent;
    return reach !== 0 ? reach : stamp(b) - stamp(a);
  })[0];
}

/** Whatever was edited last, which is where "take me back" points. */
export function lastTouched(resumes: Resume[]): Resume | null {
  if (resumes.length === 0) return null;
  return [...resumes].sort((a, b) => stamp(b) - stamp(a))[0];
}
