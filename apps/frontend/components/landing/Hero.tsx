import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import ResumeSheet from "@/components/landing/ResumeSheet";

export default function Hero() {
  const t = useTranslations("landing.hero");

  return (
    <section className="mx-auto max-w-[40rem] px-5 pb-16 pt-16 md:pb-20 md:pt-24">
      <p className="landing-kicker">
        <span className="landing-kicker-mark" aria-hidden="true" />
        {t("kicker")}
      </p>
      <h1 className="font-display mt-4 max-w-[18ch] text-[1.9rem] leading-[1.18] tracking-tight text-ink md:text-[2.65rem]">
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
      <ResumeSheet />
    </section>
  );
}
