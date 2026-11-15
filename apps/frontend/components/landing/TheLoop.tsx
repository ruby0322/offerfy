import { useTranslations } from "next-intl";
import { FindArt, ForkArt, KeepArt, ShipArt } from "./loop-art";

/**
 * The four steps of one application, on one bordered track.
 *
 * One track rather than four columns, so the steps read as a single run. Each
 * cell now opens with a vignette (see `loop-art.tsx`) and its body is one
 * sentence: the picture carries the shape of the step and the feature rows
 * below carry the detail, which is what made room to shorten these.
 *
 * **No return arrow.** The track used to end in a dashed leg that ran back
 * under the four cells and turned up into step 01. It drew the cycle
 * accurately and still had to go: at the width of a phone it was a dotted
 * line to nowhere, and the sentence it was there to illustrate says the same
 * thing in words that survive a reflow. The closing strip keeps the sentence.
 */
export default function TheLoop() {
  const t = useTranslations("landing.loop");
  const steps = [
    { title: t("findTitle"), body: t("findBody"), art: <FindArt /> },
    { title: t("aimTitle"), body: t("aimBody"), art: <ForkArt /> },
    { title: t("shipTitle"), body: t("shipBody"), art: <ShipArt /> },
    { title: t("keepTitle"), body: t("keepBody"), art: <KeepArt /> },
  ];
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      <div className="landing-loop-track">
        <ol className="landing-loop">
          {steps.map((step, index) => (
            <li key={step.title} className="landing-loop-step">
              {step.art}
              <span className="landing-loop-index" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </span>
              <h3 className="landing-loop-title font-display">{step.title}</h3>
              <p className="landing-loop-body">{step.body}</p>
            </li>
          ))}
        </ol>
        <div className="landing-loop-return">
          <p className="landing-loop-return-text">{t("returnLabel")}</p>
        </div>
      </div>
    </section>
  );
}
