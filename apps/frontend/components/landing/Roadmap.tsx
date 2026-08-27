import { useTranslations } from "next-intl";

export default function Roadmap() {
  const t = useTranslations("landing.roadmap");

  return (
    <section className="mx-auto max-w-[72rem] px-5 pb-12 pt-4 md:pb-16 md:pt-8">
      <div className="landing-roadmap">
        <div>
          <h2 className="font-display">{t("nowTitle")}</h2>
          <ul>
            <li>{t("nowEditor")}</li>
            <li>{t("nowAts")}</li>
            <li>{t("nowAnon")}</li>
          </ul>
        </div>
        <div>
          <h2 className="font-display">{t("nextTitle")}</h2>
          <ul>
            <li>{t("nextSearch")}</li>
            <li>{t("nextTailor")}</li>
            <li>{t("nextApply")}</li>
            <li>{t("nextAb")}</li>
          </ul>
        </div>
      </div>
      <p className="landing-ats-note mt-10 max-w-[42rem] text-sm leading-relaxed text-muted-foreground md:mt-12">
        {t("atsNote")}
      </p>
    </section>
  );
}
