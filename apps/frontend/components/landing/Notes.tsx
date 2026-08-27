import { useTranslations } from "next-intl";

export default function Notes() {
  const t = useTranslations("landing.notes");
  return (
    <section className="landing-notes mx-auto max-w-[72rem] border-t border-rule px-5 py-16 md:py-24">
      <div className="landing-notes-grid">
        <p>{t("typst")}</p>
        <p>{t("antiSlop")}</p>
      </div>
    </section>
  );
}
