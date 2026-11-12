import { useTranslations } from "next-intl";

export default function HonestBlock() {
  const t = useTranslations("landing.honest");
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <div className="landing-honest">
        <h2 className="landing-honest-title font-display">{t("title")}</h2>
        <p className="landing-honest-body">{t("body")}</p>
      </div>
    </section>
  );
}
