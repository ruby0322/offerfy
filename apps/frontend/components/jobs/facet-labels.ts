import type { useTranslations } from "next-intl";

/**
 * Facet value -> message key, for every closed vocabulary the catalogue has.
 *
 * Extracted from the old JobSearchForm when the filter bar replaced it: the
 * dashboard and the preference editor label the same vocabularies, and they
 * should not have to import a form to do it.
 */
export type JobsMessageKey = Parameters<ReturnType<typeof useTranslations<"jobs">>>[0];

/**
 * Every country the facets endpoint can return, not the subset we started
 * with. `label()` in FilterBar falls back to the raw value, so a missing
 * entry does not throw -- it quietly renders "MY" where Malaysia belongs,
 * which is how five of these (MX, MY, PH, TH, VN) reached production. The
 * vocabulary lives in job_taxonomy.json, so when a needle is added there
 * this map has to gain the code in the same change.
 *
 * The Latin American codes and the `latam` region are wired ahead of their
 * postings: another session has the needles written and the strings drafted
 * in all three locales, so the labels go in now rather than after the
 * re-derive makes the bare codes visible. facet-labels.test.ts fails the
 * moment a vocabulary lands without its label, which is how this is meant
 * to be caught from now on.
 */
export const COUNTRY_KEYS: Record<string, JobsMessageKey> = {
  AR: "countryAr",
  AU: "countryAu",
  BR: "countryBr",
  CA: "countryCa",
  CH: "countryCh",
  CL: "countryCl",
  CN: "countryCn",
  CO: "countryCo",
  DE: "countryDe",
  FR: "countryFr",
  GB: "countryGb",
  HK: "countryHk",
  ID: "countryId",
  IE: "countryIe",
  IL: "countryIl",
  IN: "countryIn",
  JP: "countryJp",
  KR: "countryKr",
  MX: "countryMx",
  MY: "countryMy",
  NL: "countryNl",
  PE: "countryPe",
  PH: "countryPh",
  PL: "countryPl",
  SG: "countrySg",
  TH: "countryTh",
  TW: "countryTw",
  US: "countryUs",
  VN: "countryVn",
};

export const REGION_KEYS: Record<string, JobsMessageKey> = {
  north_america: "regionNorthAmerica",
  emea: "regionEmea",
  apac: "regionApac",
  latam: "regionLatam",
};

export const SENIORITY_KEYS: Record<string, JobsMessageKey> = {
  intern: "seniorityIntern",
  new_grad: "seniorityNewGrad",
  junior: "seniorityJunior",
  mid: "seniorityMid",
  senior: "senioritySenior",
  staff_plus: "seniorityStaffPlus",
  manager: "seniorityManager",
  unknown: "seniorityUnknown",
};

export const FUNCTION_KEYS: Record<string, JobsMessageKey> = {
  software: "functionSoftware",
  backend: "functionBackend",
  frontend: "functionFrontend",
  fullstack: "functionFullstack",
  ml: "functionMl",
  data: "functionData",
  infra: "functionInfra",
  security: "functionSecurity",
  hardware: "functionHardware",
  product: "functionProduct",
  design: "functionDesign",
  other: "functionOther",
};
