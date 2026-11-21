import { useTranslations } from "next-intl";
import { NightArt } from "./autopilot-art";

type EventCopy = { role: string; company: string };

const TIMES = ["01:12", "02:40", "04:05"];

/**
 * One night, read twice: the assistant working, and what you wake up to.
 *
 * **Both halves at once, not a control.** An earlier cut put the two views
 * behind tabs; nothing else on this page asks the reader to operate anything,
 * and a tab strip on a landing section reads like an app that wandered in.
 * Side by side the contrast does the work the switch was there to do, and on
 * a phone the halves stack so the night still comes before the morning.
 *
 * The asymmetry between the halves is deliberate and carries the argument.
 * The night is *drawn* — nobody watched it happen. The morning is the actual
 * list, the same rows the product shows, because the point of the section is
 * that what you wake up to is not an illustration of a record. It is the
 * record.
 *
 * No client component and no runtime: the drawing's animation lives in
 * globals.css behind `prefers-reduced-motion: no-preference`, and the resting
 * state is the finished state — the same contract `loop-art.tsx` keeps.
 */
export default function Autopilot() {
  const t = useTranslations("landing.autopilot");
  const events = t.raw("events") as EventCopy[];

  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="landing-section-title font-display">{t("title")}</h2>
      <p className="landing-autopilot-lede">{t("lede")}</p>

      <div className="landing-autopilot">
        <div className="landing-autopilot-half">
          <p className="landing-kicker">
            <span className="landing-kicker-mark" aria-hidden="true" />
            {t("viewNight")}
          </p>
          <p className="landing-autopilot-caption">{t("nightCaption")}</p>
          <div className="landing-autopilot-figure">
            <NightArt />
          </div>
        </div>

        <span className="landing-autopilot-seam" aria-hidden="true" />

        <div className="landing-autopilot-half">
          <p className="landing-kicker">
            <span className="landing-kicker-mark" aria-hidden="true" />
            {t("viewMorning")}
          </p>
          <p className="landing-autopilot-caption">{t("morningCaption")}</p>
          <ol className="landing-autopilot-rows">
            {events.map((event, index) => (
              <li key={event.role} className="landing-autopilot-row">
                <span className="landing-autopilot-row-when">{TIMES[index]}</span>
                <span className="landing-autopilot-row-role">{event.role}</span>
                <span className="landing-autopilot-row-company">{event.company}</span>
                <span className="landing-autopilot-row-did">{t("did")}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
