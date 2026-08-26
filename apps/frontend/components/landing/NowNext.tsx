import { useTranslations } from "next-intl";

export default function NowNext() {
  const t = useTranslations("landing.nowNext");
  const nowItems = [t("nowMaster"), t("nowAts"), t("nowAnon")];
  const nextItems = [t("nextSearch"), t("nextTailor"), t("nextApply"), t("nextAb")];

  return (
    <section className="mx-auto max-w-6xl px-5 py-16">
      <div className="grid gap-4 md:grid-cols-2">
        <article className="landing-card">
          <h2 className="text-2xl font-semibold text-white">{t("nowTitle")}</h2>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-white/80">
            {nowItems.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="accent-dot mt-1.5 shrink-0" aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </article>
        <article className="landing-card">
          <h2 className="text-2xl font-semibold text-white">{t("nextTitle")}</h2>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-white/80">
            {nextItems.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full bg-teal" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </article>
      </div>
      <p className="mt-6 max-w-3xl text-sm text-white/60">{t("atsNote")}</p>
    </section>
  );
}
