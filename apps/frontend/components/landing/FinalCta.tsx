import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";

/**
 * The closing block: it is free now, and that is all anyone can honestly
 * promise.
 *
 * **No deadline, and no "free forever".** The Terms are explicit that Phase 1
 * promises nothing about features or allowances staying as they are, which
 * cuts both ways: a page that invents a date the price rises is lying, and a
 * page that says "free forever" is making a commitment the Terms disclaim.
 * The present tense plus the honest caveat is the strongest thing here that is
 * also true -- the same line PricingPage.tsx draws, for the same reason. If a
 * real end date is ever decided, this is where it goes.
 *
 * The allowance link goes to /pricing, which reads the live numbers from the
 * API rather than from the message files, so no figure on this page can drift
 * away from the enforced one.
 */
export default function FinalCta() {
  const t = useTranslations("landing.cta");
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <div className="landing-cta-block">
        <p className="landing-kicker">
          <span className="landing-kicker-mark" aria-hidden="true" />
          {t("kicker")}
        </p>
        <h2 className="landing-cta-headline font-display">{t("headline")}</h2>
        <p className="landing-cta-body">{t("body")}</p>
        <div className="landing-cta-actions">
          <Link href="/upload" className="landing-cta">
            {t("primary")}
          </Link>
          <Link href="/jobs" className="landing-cta-ghost">
            {t("secondary")}
          </Link>
        </div>
        <p className="landing-cta-fine">
          <Link href="/pricing" className="landing-cta-link">
            {t("allowance")}
          </Link>
        </p>
      </div>
    </section>
  );
}
