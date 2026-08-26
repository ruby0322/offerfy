import { useTranslations } from "next-intl";

export default function HowItWorks() {
  const t = useTranslations("landing.how");
  const steps = [
    t("stepMatch"),
    t("stepEnhance"),
    t("stepTailor"),
    t("stepConfirm"),
    t("stepTrack"),
  ];
  const grades = [t("interestLow"), t("interestMid"), t("interestHigh")];

  return (
    <section className="mx-auto max-w-6xl px-5 py-16">
      <h2 className="text-3xl font-semibold text-white">{t("title")}</h2>
      <p className="mt-3 max-w-2xl text-white/75">{t("lead")}</p>
      <ol className="mt-8 grid gap-3 md:grid-cols-5">
        {steps.map((step, index) => (
          <li key={step} className="landing-card">
            <span className="text-xs text-teal">{String(index + 1).padStart(2, "0")}</span>
            <p className="mt-2 font-medium text-white">{step}</p>
            {index !== 1 ? (
              <span className="coming-pill mt-2">{t("coming")}</span>
            ) : null}
          </li>
        ))}
      </ol>
      <div className="landing-card mt-8">
        <h3 className="font-semibold text-white">
          {t("interestTitle")}
          <span className="coming-pill">{t("coming")}</span>
        </h3>
        <ul className="mt-3 grid gap-2 text-sm text-white/75 md:grid-cols-3">
          {grades.map((grade) => (
            <li key={grade}>{grade}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
