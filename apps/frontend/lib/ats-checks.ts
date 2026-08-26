export const ATS_CHECK_NAMES = [
  "text_extractable",
  "single_column",
  "contact_in_body",
  "standard_headings",
  "dates_machine_readable",
  "no_embedded_images_as_text",
  "fonts_embedded",
  "parse_roundtrip_ok",
] as const;

export type AtsCheckName = (typeof ATS_CHECK_NAMES)[number];
