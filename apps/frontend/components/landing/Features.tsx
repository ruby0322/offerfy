import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { fetchJobEmployerCount } from "@/lib/jobs";
import { EMPLOYER_COUNT } from "@/lib/seo";
import { AgentArt, DigestArt, IngestArt, TrackingArt } from "./feature-art";

/**
 * The four things the product does, one row each.
 *
 * Rows rather than a card grid: each of these needs a paragraph to be
 * believed, and four paragraphs in four cards is a wall. The figure changes
 * sides down the page, which globals.css does with grid-column rather than by
 * reordering this markup -- the copy is first in source on every row.
 *
 * The section's heading is visually hidden on purpose. Each row owns an <h3>,
 * and an <h3> with no <h2> above it is a hole in the outline; the page does
 * not need a fifth visible title to close it.
 *
 * Every claim here is checked against the code that makes it true, and two of
 * them are easy to get wrong later: the catalogue is read every six hours by
 * `offerfy-ingest.timer` (four passes a day, plus a Workday-only pass at
 * 03:40), and the digest is written to the dashboard -- Offerfy sends no mail,
 * so no line here may imply an inbox. See AGENTS.md section 8 and
 * `app/jobs/build_digests.py`.
 *
 * **The employer count is read live, never typed in.** It shipped as a
 * literal 91 and was wrong inside a day: a parallel branch added a source and
 * the board files went to 97 without anyone touching this file. Same fetch
 * CompanyWall uses, so the two cannot disagree, and the fallback is the same
 * EMPLOYER_COUNT constant.
 */
export default async function Features() {
  const t = await getTranslations("landing.features");
  const employers = await fetchJobEmployerCount();
  const rows = [
    {
      key: "ingest",
      kicker: t("ingestKicker"),
      title: t("ingestTitle"),
      body: t("ingestBody", { count: employers ?? EMPLOYER_COUNT }),
      note: (
        <p className="landing-feature-note">
          {t("ingestNote")}{" "}
          <Link href="/contact" className="landing-cta-link">
            {t("ingestNoteLink")}
          </Link>
        </p>
      ),
      art: <IngestArt title={t("ingestFigure")} />,
    },
    {
      key: "digest",
      kicker: t("digestKicker"),
      title: t("digestTitle"),
      body: t("digestBody"),
      note: null,
      art: (
        <DigestArt title={t("digestFigure")} />
      ),
    },
    {
      key: "agent",
      kicker: t("agentKicker"),
      title: t("agentTitle"),
      body: t("agentBody"),
      note: null,
      art: (
        <AgentArt
          title={t("agentFigure")}
          layers={[
            t("agentLayerFacts"),
            t("agentLayerPosting"),
            t("agentLayerMemory"),
            t("agentLayerWindow"),
          ]}
          diffOld={t("agentDiffOld")}
          diffNew={t("agentDiffNew")}
          tools={[
            t("agentToolPosting"),
            t("agentToolTypst"),
            t("agentToolAts"),
            t("agentToolMaster"),
          ]}
        />
      ),
    },
    {
      key: "tracking",
      kicker: t("trackKicker"),
      title: t("trackTitle"),
      body: t("trackBody"),
      note: null,
      art: (
        <TrackingArt
          title={t("trackFigure")}
          interview={t("trackInterview")}
          replied={t("trackReplied")}
          silent={t("trackSilent")}
        />
      ),
    },
  ];
  return (
    <section className="landing-section mx-auto max-w-[72rem] px-5">
      <h2 className="sr-only">{t("title")}</h2>
      <div className="landing-features">
        {rows.map((row, index) => (
          <div
            key={row.key}
            className={`landing-feature-row${index % 2 === 1 ? " landing-feature-flip" : ""}`}
          >
            <div className="landing-feature-copy">
              <p className="landing-kicker">
                <span className="landing-kicker-mark" aria-hidden="true" />
                {row.kicker}
              </p>
              <h3 className="landing-feature-title font-display">{row.title}</h3>
              <p className="landing-feature-body">{row.body}</p>
              {row.note}
            </div>
            <div className="landing-feature-figure">{row.art}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
