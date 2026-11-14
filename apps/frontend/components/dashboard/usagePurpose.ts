/**
 * The backend's spend purposes, in human words and in a stable colour.
 *
 * `llm_calls.purpose` is a fixed vocabulary written for the people who read
 * the ledger, not for the person whose money it was: "extract" means nothing
 * until you are told it is the model reading your resume so matching can use
 * it, and nobody guesses that "repair" is the retry after an edit came back
 * malformed -- which is also the only way someone learns that retries cost
 * them anything.
 *
 * `other` exists because the breakdown must sum to the meter. A purpose the
 * backend adds later lands there rather than vanishing from a chart whose
 * parts are supposed to account for the whole.
 */
export const PURPOSE_KEY: Record<string, string> = {
  chat: "purposeChat",
  import: "purposeImport",
  extract: "purposeExtract",
  match: "purposeMatch",
  digest: "purposeDigest",
  vision: "purposeVision",
  profile: "purposeProfile",
  repair: "purposeRepair",
  other: "purposeOther",
};

/**
 * Chart colours, from the theme's own chart ramp so both themes are handled.
 * Clay leads because editing is almost always the largest slice; the
 * background jobs take the cooler end.
 */
export const PURPOSE_COLOR: Record<string, string> = {
  chat: "var(--chart-1)",
  import: "var(--chart-3)",
  extract: "var(--chart-4)",
  digest: "var(--chart-2)",
  match: "var(--chart-2)",
  vision: "var(--chart-3)",
  profile: "var(--chart-5)",
  repair: "var(--chart-5)",
  other: "var(--chart-5)",
};

export function purposeKey(purpose: string): string {
  return PURPOSE_KEY[purpose] ?? "purposeOther";
}

export function purposeColor(purpose: string): string {
  return PURPOSE_COLOR[purpose] ?? "var(--chart-5)";
}
