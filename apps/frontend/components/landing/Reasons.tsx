import { useTranslations } from "next-intl";

export default function Reasons() {
  const t = useTranslations("landing.reasons");
  const keys = ["one", "two", "three"] as const;

  return (
    <section className="mx-auto max-w-[44rem] border-t border-rule px-5 py-16 md:py-20">
      <ul className="landing-reasons">
        {keys.map((key) => (
          <li key={key}>{t(key)}</li>
        ))}
      </ul>
    </section>
  );
}
