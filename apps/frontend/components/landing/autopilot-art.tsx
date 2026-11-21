import { EMPLOYER_MARKS } from "@/components/employer-marks";

/**
 * The night, drawn in the same hand as the rest of this page.
 *
 * An earlier cut of this had a little character with a face, borrowed from
 * the social reel. Nothing on this page draws people: the figures here are
 * diagrams — employer marks in rounded chips, a clay junction, a translucent
 * band for flow, a sheet panel with rows on it — and a smiling tile landed in
 * the middle of them looking like it came from somewhere else. So this uses
 * `feature-art.tsx`'s vocabulary exactly: the same 212x128 viewBox, the same
 * 20px chips at scale 0.5417, the same 1px clay connectors.
 *
 * The three companies are the three rows in the list beside it, in order, so
 * the drawing and the record are visibly the same night.
 *
 * **The resting state is the finished state**: all three rows are drawn and
 * opaque, and the landing stagger in globals.css is additive and sits behind
 * `prefers-reduced-motion: no-preference`.
 *
 * `aria-hidden`, like `loop-art.tsx`: the list next to it already says every
 * one of these things in words.
 */

const COMPANIES = ["Stripe", "Notion", "NVIDIA"];

export function NightArt() {
  return (
    <svg
      className="landing-art landing-autopilot-art"
      viewBox="0 0 212 128"
      role="presentation"
      aria-hidden="true"
      focusable="false"
      data-art="night"
    >
      {/* The postings the assistant is reading, as employer chips. */}
      {COMPANIES.map((company, index) => {
        const mark = EMPLOYER_MARKS[company];
        const y = 10 + index * 38;
        return (
          <g key={company}>
            <rect x="4" y={y + 7} width="20" height="20" rx="4" fill="none" stroke="var(--rule)" />
            {mark ? (
              <g transform={`translate(7.5, ${y + 10.5}) scale(0.5417)`}>
                <path d={mark.d} fill="var(--ink)" fillRule={mark.fillRule} />
              </g>
            ) : null}
            {/* Each posting curves in to the junction. */}
            <path
              d={`M28 ${y + 17} C 44 ${y + 17}, 48 64, 62 64`}
              fill="none"
              stroke="var(--rule)"
              strokeWidth="1"
            />
          </g>
        );
      })}

      {/* The junction, and the work travelling from it into Offerfy. */}
      <circle cx="64" cy="64" r="3" fill="var(--clay)" />
      <path d="M68 64H90" stroke="var(--clay)" strokeWidth="12" opacity="0.18" fill="none" />
      <path
        className="landing-art-autopilot-travel"
        d="M68 64h10"
        fill="none"
        stroke="var(--clay)"
        strokeWidth="2"
        strokeLinecap="round"
      />

      {/* Offerfy: the sheet the night's work lands on. */}
      <rect x="94" y="10" width="112" height="108" rx="3" fill="var(--sheet)" stroke="var(--rule)" />
      <line x1="94" y1="28" x2="206" y2="28" stroke="var(--rule)" />
      <circle cx="103" cy="19" r="2" fill="var(--rule)" />
      <circle cx="111" cy="19" r="2" fill="var(--rule)" />
      <circle cx="119" cy="19" r="2" fill="var(--rule)" />

      {COMPANIES.map((company, index) => (
        <g
          key={`row-${company}`}
          className="landing-art-autopilot-row"
          style={{ animationDelay: `${0.5 + index * 0.85}s` }}
        >
          <rect
            x="102"
            y={38 + index * 26}
            width="96"
            height="20"
            rx="2"
            fill="var(--landing-art-tint)"
            stroke="var(--rule)"
          />
          <line
            x1="109"
            y1={48 + index * 26}
            x2={109 + 52 - index * 6}
            y2={48 + index * 26}
            stroke="var(--clay)"
            strokeWidth="2"
          />
          <circle cx="190" cy={48 + index * 26} r="2.5" fill="var(--clay)" opacity="0.6" />
        </g>
      ))}

      {/* A moon, so the figure says "overnight" without borrowing the caption. */}
      <path
        d="M203 112 a6 6 0 1 1 -7.3 -7.4 a4.8 4.8 0 0 0 7.3 7.4 z"
        fill="none"
        stroke="var(--rule)"
        strokeWidth="1.1"
      />
    </svg>
  );
}
