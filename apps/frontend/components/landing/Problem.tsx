import { useTranslations } from "next-intl";

export default function Problem() {
  const t = useTranslations("landing.problem");
  const cards = [
    { title: t("fragmentedTitle"), body: t("fragmentedBody") },
    { title: t("switchingTitle"), body: t("switchingBody") },
    { title: t("qualityTitle"), body: t("qualityBody") },
  ];

  return (
    <section className="mx-auto max-w-6xl px-5 py-16">
      <h2 className="text-3xl font-semibold text-white">{t("title")}</h2>
      <p className="mt-3 max-w-2xl text-white/75">{t("lead")}</p>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {cards.map((card) => (
          <article key={card.title} className="landing-card">
            <div className="accent-dot mb-3" aria-hidden="true" />
            <h3 className="text-lg font-semibold text-white">{card.title}</h3>
            <p className="mt-2 text-sm leading-6 text-white/75">{card.body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
