import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";

export default async function CtaRow() {
  const t = await getTranslations("landing.hero");
  return (
    <p className="blog-cta">
      <Link href="/upload" className="blog-cta-primary">
        {t("ctaUpload")}
      </Link>
      <Link href="/create" className="blog-cta-secondary">
        {t("ctaCreate")}
      </Link>
    </p>
  );
}
