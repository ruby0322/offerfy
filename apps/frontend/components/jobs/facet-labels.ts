import type { useTranslations } from "next-intl";

/**
 * Facet value -> message key, for every closed vocabulary the catalogue has.
 *
 * Extracted from the old JobSearchForm when the filter bar replaced it: the
 * dashboard and the preference editor label the same vocabularies, and they
 * should not have to import a form to do it.
 */
export type JobsMessageKey = Parameters<ReturnType<typeof useTranslations<"jobs">>>[0];

export const COUNTRY_KEYS: Record<string, JobsMessageKey> = {
  AU: "countryAu",
  CA: "countryCa",
  CH: "countryCh",
  CN: "countryCn",
  DE: "countryDe",
  FR: "countryFr",
  GB: "countryGb",
  HK: "countryHk",
  IE: "countryIe",
  IL: "countryIl",
  IN: "countryIn",
  JP: "countryJp",
  KR: "countryKr",
  NL: "countryNl",
  PL: "countryPl",
  SG: "countrySg",
  TW: "countryTw",
  US: "countryUs",
};

export const REGION_KEYS: Record<string, JobsMessageKey> = {
  north_america: "regionNorthAmerica",
  emea: "regionEmea",
  apac: "regionApac",
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
