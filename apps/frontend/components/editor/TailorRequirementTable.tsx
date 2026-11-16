"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import type { RequirementStatus, TailorRequirementMap } from "@/lib/tailor-actions";

/**
 * The posting's requirements next to what the resume says about each one.
 *
 * It describes, it does not grade. There is no score, no percentage and no
 * total "fit" anywhere, and the status column is one neutral ink rather than
 * a green/red pass-fail -- `not_in_resume` is a fact about the document, not
 * a mark against the person, and colouring it red would turn a working list
 * into a report card. The three marks differ in shape instead: filled, half,
 * hollow.
 */
const MARKS: Record<RequirementStatus, string> = {
  in_resume: "●",
  partly_in_resume: "◐",
  not_in_resume: "○",
  unclear: "—",
};

export default function TailorRequirementTable({ map }: { map: TailorRequirementMap }) {
  const t = useTranslations("editor");

  const statusLabel = (status: RequirementStatus): string => {
    if (status === "in_resume") return t("reqStatusInResume");
    if (status === "partly_in_resume") return t("reqStatusPartly");
    if (status === "not_in_resume") return t("reqStatusNotInResume");
    return t("reqStatusUnclear");
  };

  return (
    <div className="mt-2 pl-8">
      <p className="text-xs text-muted-foreground">{t("reqSummary", { count: map.total })}</p>
      {/* `table-fixed` with percentage widths rather than fixed px: the
          labels are written in zh-TW first and CJK runs about 1.5x wider
          than the English, so any px column would wrap in one locale and
          look empty in the other. */}
      <table className="mt-2 w-full table-fixed border-collapse text-xs">
        <thead>
          <tr className="border-b border-rule text-left text-muted-foreground">
            <th className="w-[36%] py-1.5 pr-2 font-medium">{t("reqColRequirement")}</th>
            <th className="w-[42%] py-1.5 pr-2 font-medium">{t("reqColResume")}</th>
            <th className="w-[22%] py-1.5 font-medium">{t("reqColStatus")}</th>
          </tr>
        </thead>
        <tbody>
          {map.items.map((item, index) => (
            <tr
              key={`${item.requirement}-${index}`}
              className="border-b border-rule last:border-b-0 align-top"
            >
              <td className="py-2 pr-2 text-foreground">{item.requirement}</td>
              <td className={cn("py-2 pr-2", item.resumeQuote ? "text-muted-foreground" : "text-muted-foreground")}>
                {/* The quote when the server found it verbatim in the
                    resume, the model's own note otherwise, and a plain
                    "nothing here" when there is neither -- never an invented
                    paraphrase of a document we did not match. */}
                {item.resumeQuote
                  ? `「${item.resumeQuote}」`
                  : item.note || t("reqQuoteMissing")}
              </td>
              <td className="whitespace-nowrap py-2 text-muted-foreground">
                <span aria-hidden="true">{MARKS[item.status]}</span>{" "}
                {statusLabel(item.status)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
