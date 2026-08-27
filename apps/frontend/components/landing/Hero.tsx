import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import EditorMock from "@/components/landing/EditorMock";

export default function Hero() {
  const t = useTranslations("landing.hero");
  return (
    <section className="landing-hero mx-auto max-w-[72rem] px-5 pb-16 pt-16 md:pb-20 md:pt-24">
      <div className="landing-hero-copy">
        <p className="landing-kicker">
          <span className="landing-kicker-mark" aria-hidden="true" />
          {t("kicker")}
        </p>
        <h1 className="font-display mt-4 max-w-[22ch] text-[1.9rem] leading-[1.18] tracking-tight text-ink md:text-[2.65rem]">
          {t("headline")}
        </h1>
        <p className="mt-4 max-w-[34ch] text-base leading-relaxed text-muted-foreground md:text-lg">
          {t("sub")}
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <Link href="/create" className="landing-cta">
            {t("ctaCreate")}
          </Link>
          <Link href="/upload" className="landing-cta-link">
            {t("ctaUpload")}
          </Link>
        </div>
      </div>
      <EditorMock />
    </section>
  );
}
