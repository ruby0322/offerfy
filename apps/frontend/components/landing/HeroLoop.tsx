import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";

export default function HeroLoop() {
  const t = useTranslations("landing.hero");
  const nodes = [
    t("loopSearch"),
    t("loopEnhance"),
    t("loopTailor"),
    t("loopApply"),
  ];

  return (
    <section className="mx-auto max-w-6xl px-5 pb-16 pt-14">
      <p className="hero-kicker">
        <span className="accent-dot" aria-hidden="true" />
        {t("kicker")}
      </p>
      <h1 className="mt-4 max-w-3xl text-4xl font-semibold leading-tight tracking-tight text-white md:text-5xl">
        {t("headline")}
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-white/80">{t("sub")}</p>

      <div className="loop-row mt-10">
        {nodes.map((label, index) => (
          <div key={label} className="contents">
            <div className="loop-node">
              <span className="text-xs uppercase tracking-wider text-teal">
                {String(index + 1).padStart(2, "0")}
              </span>
              <strong className="mt-1 text-lg">{label}</strong>
            </div>
            {index < nodes.length - 1 ? (
              <span className="loop-arrow hidden md:block" aria-hidden="true">
                →
              </span>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mt-10 flex flex-wrap gap-3">
        <Link href="/create" className="cta-primary">
          {t("ctaCreate")}
        </Link>
        <Link href="/upload" className="cta-secondary">
          {t("ctaUpload")}
        </Link>
      </div>
      <p className="mt-4 max-w-xl text-sm text-white/65">{t("microcopy")}</p>
    </section>
  );
}
