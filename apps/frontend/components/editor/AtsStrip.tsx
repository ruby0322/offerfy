"use client";

import { useTranslations } from "next-intl";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { ATS_CHECK_NAMES } from "@/lib/ats-checks";
import type { AtsReport } from "@/lib/api";

type Props = {
  report: AtsReport | null;
};

export default function AtsStrip({ report }: Props) {
  const t = useTranslations("ats");
  const byName = new Map((report?.checks ?? []).map((check) => [check.name, check.passed]));
  const known = ATS_CHECK_NAMES.map((name) => byName.get(name)).filter(
    (value): value is boolean => value != null,
  );
  const passedCount = known.filter(Boolean).length;
  const summary =
    known.length > 0 ? t("summary", { passed: passedCount, total: ATS_CHECK_NAMES.length }) : t("pending");

  return (
    <Accordion
      type="single"
      collapsible
      className="shrink-0 border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
    >
      <AccordionItem value="ats" className="border-b-0">
        <AccordionTrigger className="px-4 py-3 hover:no-underline">
          <span className="flex items-center gap-2">
            {t("title")}
            <Badge variant="secondary" className="font-normal">
              {summary}
            </Badge>
          </span>
        </AccordionTrigger>
        <AccordionContent className="px-4">
          <p className="mb-3 text-xs leading-5 text-gray-500 dark:text-gray-400">{t("disclaimer")}</p>
          {ATS_CHECK_NAMES.map((name) => {
            const passed = byName.get(name);
            return (
              <div
                key={name}
                className="flex justify-between gap-3 border-b border-gray-100 py-1.5 text-sm last:border-b-0 dark:border-gray-800"
              >
                <span>{t(`checks.${name}`)}</span>
                {passed == null ? (
                  <span className="text-gray-400">{t("pending")}</span>
                ) : (
                    <span className={passed ? "font-semibold text-emerald-700 dark:text-emerald-400" : "font-semibold text-red-600 dark:text-red-400"}>
                    {passed ? t("pass") : t("fail")}
                  </span>
                )}
              </div>
            );
          })}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
