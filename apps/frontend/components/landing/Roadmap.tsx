import { useTranslations } from "next-intl";

export default function Roadmap() {
  const t = useTranslations("landing.roadmap");

  return (
    <section className="mx-auto max-w-[44rem] px-5 pb-8 pt-8 md:pb-12 md:pt-4">
      <div className="landing-roadmap">
        <div>
          <h2 className="landing-display">{t("nowTitle")}</h2>
          <ul>
            <li>{t("nowEditor")}</li>
            <li>{t("nowAts")}</li>
            <li>{t("nowAnon")}</li>
          </ul>
        </div>
        <div>
          <h2 className="landing-display">{t("nextTitle")}</h2>
          <ul>
            <li>{t("nextSearch")}</li>
            <li>{t("nextTailor")}</li>
            <li>{t("nextApply")}</li>
            <li>{t("nextAb")}</li>
          </ul>
        </div>
      </div>
      <p className="mt-10 max-w-[40rem] text-sm leading-relaxed text-[var(--landing-muted)]">
        {t("atsNote")}
      </p>
    </section>
  );
}
