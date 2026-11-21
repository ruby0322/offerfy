import { getTranslations } from "next-intl/server";
import Footer from "@/components/Footer";
import ConnectPrompt from "@/components/mcp/ConnectPrompt";
import Nav from "@/components/Nav";
import JsonLd from "@/components/seo/JsonLd";
import { Link } from "@/i18n/navigation";
import { organizationJsonLd } from "@/lib/seo";

/**
 * How to point your own assistant at Offerfy.
 *
 * Shaped around one claim: paste the block at the top into a CLI or coding agent
 * and you are connected. Everything below it exists for the readers that claim
 * does not serve — claude.ai, whose connectors are added in a web form its agent
 * cannot reach, and ChatGPT, which cannot carry a static key at all.
 *
 * It reuses `legal-article` for prose rhythm rather than inventing a second set
 * of page classes; the blocks that are not prose (the prompt, the commands) use
 * utilities the way `components/settings/` does. Nav takes `variant="landing"`
 * because the nav links only render for that variant (`Nav.tsx`), and a page
 * reached from the nav that then has no nav is a dead end.
 */
export default async function McpGuide() {
  const t = await getTranslations("mcp");
  const webSteps = t.raw("webSteps") as string[];
  const groups = t.raw("groups") as { heading: string; body: string }[];
  const safety = t.raw("safetyPoints") as string[];

  return (
    <div className="landing-page">
      <Nav variant="landing" />
      <JsonLd data={organizationJsonLd()} />
      <main>
        <article className="legal-article">
          <h1 className="font-display">{t("title")}</h1>
          <p>{t("lede")}</p>

          <section>
            <h2 className="font-display">{t("promptTitle")}</h2>
            <ConnectPrompt body={t("promptBody")} />
          </section>

          <section>
            <h2 className="font-display">{t("keyTitle")}</h2>
            <p>
              {t("keyBody")}{" "}
              <Link href="/login" className="underline">
                {t("keyCta")}
              </Link>
            </p>
          </section>

          <section>
            <h2 className="font-display">{t("clientsTitle")}</h2>

            <h3 className="font-display">{t("ccTitle")}</h3>
            <p>{t("ccBody")}</p>
            {/* Wraps rather than scrolls. A command you are meant to copy, cut
                off at the right edge of its box with the rest behind a scroll
                nobody notices, is a command half the readers will paste half
                of. `pre-wrap` breaks the display, not the string. */}
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted px-3 py-2.5 font-mono text-xs leading-5">
              {t("ccCommand")}
            </pre>

            <h3 className="font-display">{t("webTitle")}</h3>
            <ol className="list-decimal pl-5">
              {webSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <p>{t("webShared")}</p>

            <h3 className="font-display">{t("gptTitle")}</h3>
            <p>{t("gptBody")}</p>

            <h3 className="font-display">{t("otherTitle")}</h3>
            <p>{t("otherBody")}</p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted px-3 py-2.5 font-mono text-xs leading-5">
              {t("otherDetail")}
            </pre>
          </section>

          <section>
            <h2 className="font-display">{t("toolsTitle")}</h2>
            <p>{t("toolsLede")}</p>
            {groups.map((group) => (
              <p key={group.heading}>
                <strong>{group.heading}</strong> — {group.body}
              </p>
            ))}
          </section>

          <section>
            <h2 className="font-display">{t("safetyTitle")}</h2>
            <ul className="list-disc pl-5">
              {safety.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
            <p>{t("auditBoundary")}</p>
          </section>
        </article>
      </main>
      <Footer variant="landing" />
    </div>
  );
}
