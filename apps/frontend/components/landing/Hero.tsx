import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import EditorMock from "@/components/landing/EditorMock";
import { markFor } from "@/components/employer-marks";

// Reused rather than re-pasted: the wall already carries this path, and two
// copies of a 2kB `d` string drift the moment one of them is corrected.
const OPENAI_MARK = markFor("OpenAI");

export default function Hero() {
  const t = useTranslations("landing.hero");
  return (
    <section className="landing-hero mx-auto max-w-[72rem] px-5 pb-16 pt-16 md:pb-20 md:pt-24">
      <div className="landing-hero-copy">
        <div className="landing-eyebrow">
          <p className="landing-kicker">
            <span className="landing-kicker-mark" aria-hidden="true" />
            {t("kicker")}
          </p>
          {/* The badge links to the announcement rather than sitting there as
              decoration: a credential a reader cannot check is just a sticker. */}
          <Link href="/blog/openai-for-startups" className="landing-badge" title={t("badgeTitle")}>
            {OPENAI_MARK ? (
              <svg className="landing-badge-mark" viewBox="0 0 24 24" aria-hidden="true">
                <path d={OPENAI_MARK.d} />
              </svg>
            ) : null}
            {t("badge")}
          </Link>
        </div>
        <h1 className="font-display mt-4 max-w-[22ch] text-[1.9rem] leading-[1.18] tracking-tight text-ink md:text-[2.65rem]">
          {t("headline")}
        </h1>
        <p className="mt-4 max-w-[38ch] text-base leading-relaxed text-muted-foreground md:text-lg">
          {t("sub")}
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <Link href="/upload" className="landing-cta">
            {t("ctaUpload")}
          </Link>
          <Link href="/jobs" className="landing-cta-ghost">
            {t("ctaJobs")}
          </Link>
          <Link href="/create" className="landing-cta-link">
            {t("ctaCreate")}
          </Link>
        </div>
      </div>
      <EditorMock />
    </section>
  );
}
