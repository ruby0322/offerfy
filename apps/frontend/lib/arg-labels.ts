/**
 * Which message labels a string argument's field.
 *
 * The projection sends the Typst argument name (`dates`, `institution`, ...) as `key`;
 * a person editing a Chinese résumé should not read English identifiers. The mapping is
 * the frontend's: the API carries no label, and a template this table has never seen
 * must still draw a row, so an unmapped key has no id and the caller shows the key.
 *
 * The keys are the ones the backend's pinned fixtures project (`synthetic-en`, `-long`,
 * `-marks`, `-tier-b`, `-zh`; `-tier-c` projects no argument).
 */

/** Argument key -> id of a message in the `editor` namespace. */
const ARG_LABEL_IDS = {
  author: "fieldsLabelAuthor",
  location: "fieldsLabelLocation",
  email: "fieldsLabelEmail",
  github: "fieldsLabelGithub",
  "personal-site": "fieldsLabelPersonalSite",
  "accent-color": "fieldsLabelAccentColor",
  paper: "fieldsLabelPaper",
  lang: "fieldsLabelLang",
  title: "fieldsLabelTitle",
  company: "fieldsLabelCompany",
  dates: "fieldsLabelDates",
  "start-date": "fieldsLabelStartDate",
  "end-date": "fieldsLabelEndDate",
  institution: "fieldsLabelInstitution",
  degree: "fieldsLabelDegree",
  role: "fieldsLabelRole",
  name: "fieldsLabelName",
  url: "fieldsLabelUrl",
  // `acorn_resume` and `ats_friendly_resume` emitters (apps/backend/app/services/emitters),
  // which no pinned fixture exercises.
  organization: "fieldsLabelOrganization",
  date: "fieldsLabelDate",
  contacts: "fieldsLabelContacts",
  technologies: "fieldsLabelTechnologies",
  "text-color": "fieldsLabelTextColor",
  // Keys the packages' own `template/main.typ` examples show the model (chat.py inlines
  // them), and that the projection surfaces as rows: acorn-resume 0.1.0 and
  // ats-friendly-resume 0.1.1.
  "live-url": "fieldsLabelLiveUrl",
  "repo-url": "fieldsLabelRepoUrl",
  gpa: "fieldsLabelGpa",
  "tech-used": "fieldsLabelTechUsed",
  // Same two examples, and `font` is a string argument in both -- so it reaches a row
  // even though `is_style_key` keeps its siblings (`accent-color` aside) out. These
  // three are the rest of what those examples project; the table now covers every key
  // an Offerfy emitter writes and every key the examples show, and the fallback owns
  // the unbounded tail of the other ~93 packages.
  font: "fieldsLabelFont",
  linkedin: "fieldsLabelLinkedin",
  portfolio: "fieldsLabelPortfolio",
} as const;

export type ArgLabelId = (typeof ARG_LABEL_IDS)[keyof typeof ARG_LABEL_IDS];

/**
 * The message id for an argument key, or null when there is none. `hasOwn`, because a
 * key is data from the document and `ARG_LABEL_IDS["constructor"]` is a function.
 */
export function argLabelId(key: string | null | undefined): ArgLabelId | null {
  if (typeof key !== "string" || !Object.hasOwn(ARG_LABEL_IDS, key)) return null;
  return ARG_LABEL_IDS[key as keyof typeof ARG_LABEL_IDS];
}

/** Every message id this module can return, for the locale-parity test. */
export const ARG_LABEL_MESSAGE_IDS: readonly ArgLabelId[] = Object.values(ARG_LABEL_IDS);
