"use client";

import { useTranslations } from "next-intl";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ATS_CHECK_NAMES, type AtsCheckName } from "@/lib/ats-checks";
import type { AtsReport } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  report: AtsReport | null;
  onFix?: (name: AtsCheckName) => void;
  fixing?: boolean;
};

export default function AtsStrip({ report, onFix, fixing = false }: Props) {
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
      className="shrink-0 border-t border-border bg-background"
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
          <p className="mb-2 text-xs leading-5 text-muted-foreground">{t("disclaimer")}</p>
          <Accordion type="multiple" className="w-full">
            {ATS_CHECK_NAMES.map((name) => {
              const passed = byName.get(name);
              return (
                <AccordionItem key={name} value={name} className="border-border">
                  <AccordionTrigger className="py-2 hover:no-underline">
                    <span className="flex min-w-0 flex-1 items-center justify-between gap-3 pr-2 text-sm font-normal">
                      <span className="min-w-0 text-left">{t(`checks.${name}`)}</span>
                      {passed == null ? (
                        <span className="shrink-0 text-muted-foreground">{t("pending")}</span>
                      ) : (
                        <span
                          className={cn(
                            "shrink-0 font-semibold",
                            passed ? "text-success" : "text-destructive",
                          )}
                        >
                          {passed ? t("pass") : t("fail")}
                        </span>
                      )}
                    </span>
                  </AccordionTrigger>
                  <AccordionContent className="pb-3">
                    {passed === false ? (
                      <div className="space-y-2">
                        <p className="text-xs leading-5 text-muted-foreground">
                          {t(`meanings.${name}`)}
                        </p>
                        {onFix ? (
                          <Button
                            type="button"
                            size="sm"
                            disabled={fixing}
                            data-testid={`ats-fix-${name}`}
                            onClick={() => onFix(name)}
                          >
                            {t("fixWithAi")}
                          </Button>
                        ) : null}
                      </div>
                    ) : (
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t(`meanings.${name}`)}
                      </p>
                    )}
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
