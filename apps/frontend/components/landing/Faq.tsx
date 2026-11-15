import { useTranslations } from "next-intl";

/**
 * The five questions someone technical asks before they will upload anything.
 *
 * `<details>` rather than a React disclosure: the browser owns the open state,
 * the keyboard behaviour and the semantics, it needs no JavaScript to work,
 * and the answers are in the HTML either way -- so they are indexed and
 * findable by in-page search even while collapsed. The first is open so the
 * pattern is obvious without a click.
 *
 * Each answer is a fact about the code, and each one is load-bearing:
 * the catalogue refresh interval (`offerfy-ingest.timer`), that edits come
 * back as a reviewable line diff (`services/edit_diff.py`), that the model is
 * the person's choice and stored on their row rather than in the browser
 * (`services/model_choice.py`, migration 022), that a tailor is a fork and
 * revisions are kept (`services/revisions.py`, which coalesces edits inside
 * 120 seconds -- hence "revision history" and not "every version"), and that
 * nothing is ever submitted on the person's behalf. Change one of these in the
 * product and this file is wrong, which is the tradeoff of answering
 * specifically instead of vaguely.
 */
export default function Faq() {
  const t = useTranslations("landing.faq");
  const items = t.raw("items") as { q: string; a: string }[];
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      <div className="landing-faq">
        {items.map((item, index) => (
          <details key={item.q} className="landing-faq-item" open={index === 0}>
            <summary className="landing-faq-q font-display">
              <span>{item.q}</span>
              <svg
                className="landing-faq-chevron"
                viewBox="0 0 16 16"
                aria-hidden="true"
                focusable="false"
              >
                <path
                  d="M4 6l4 4 4-4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </summary>
            <p className="landing-faq-a">{item.a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
