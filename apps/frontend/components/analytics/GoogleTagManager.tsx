import Script from "next/script";

/** The Google Tag Manager container for offerfy.cc.
 *
 * A container ID is public by design -- it ships in the HTML of every page --
 * so it lives here rather than in the environment. What the container loads
 * (today: Google Analytics) is configured in GTM, not in this repo, which is
 * why adding a tag there can change what the site collects without a deploy.
 * Whatever it collects, the privacy policy has to keep describing it: see
 * `legal.privacy` in messages/*.json and LEGAL_UPDATED in
 * components/legal/LegalDocument.tsx.
 */
export const GTM_CONTAINER_ID = "GTM-TNQ6QD9N";

export default function GoogleTagManager() {
  return (
    <>
      {/* dataLayer first: gtm.js creates it if absent, but a tag that fires on
          load would then miss anything we had queued. */}
      <Script id="gtm-data-layer" strategy="afterInteractive">
        {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());`}
      </Script>
      <Script
        id="gtm-loader"
        src={`https://www.googletagmanager.com/gtm.js?id=${GTM_CONTAINER_ID}`}
        strategy="afterInteractive"
      />
    </>
  );
}
