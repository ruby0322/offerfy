import { useTranslations } from "next-intl";

export default function TheLoop() {
  const t = useTranslations("landing.loop");
  const steps = [
    { title: t("findTitle"), body: t("findBody") },
    { title: t("aimTitle"), body: t("aimBody") },
    { title: t("shipTitle"), body: t("shipBody") },
    { title: t("keepTitle"), body: t("keepBody") },
  ];
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      {/* One bordered track rather than four columns, so the steps read as a
          single run; the bracket below is flush with it and turns back up into
          step 01, which is the loop closing. */}
      <div className="landing-loop-track">
        <ol className="landing-loop">
          {steps.map((step, index) => (
            <li key={step.title} className="landing-loop-step">
              <span className="landing-loop-index" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </span>
              <h3 className="landing-loop-title font-display">{step.title}</h3>
              <p className="landing-loop-body">{step.body}</p>
            </li>
          ))}
        </ol>
        <div className="landing-loop-return">
          <span className="landing-loop-arrow" aria-hidden="true" />
          <p className="landing-loop-return-text">{t("returnLabel")}</p>
        </div>
      </div>
    </section>
  );
}
