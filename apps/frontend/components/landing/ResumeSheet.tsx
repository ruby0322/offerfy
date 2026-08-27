import { useTranslations } from "next-intl";

export default function ResumeSheet() {
  const t = useTranslations("landing.sheet");

  return (
    <div className="landing-sheet mx-auto mt-12 w-full max-w-md" aria-hidden="true">
      <div className="landing-sheet-name" />
      <p className="landing-sheet-label">{t("experience")}</p>
      <div className="landing-sheet-rule w-[92%]" />
      <div className="landing-sheet-rule w-[78%]" />
      <div className="landing-sheet-rule w-[86%]" />
      <p className="landing-sheet-label">{t("education")}</p>
      <div className="landing-sheet-rule w-[70%]" />
      <div className="landing-sheet-rule w-[58%]" />
    </div>
  );
}
