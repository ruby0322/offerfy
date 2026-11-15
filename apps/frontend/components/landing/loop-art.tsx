/**
 * The four vignettes above the loop's steps.
 *
 * Each draws its step's mechanism rather than an icon of its subject: the
 * filter settling on one posting, the copy peeling off the master with the
 * posting clipped to it, the compiled PDF clearing its checks, the sharpened
 * line going back into the master. 1px clay line art on the sheet, the same
 * weight as the rules around them, so the track still reads as one run.
 *
 * **The resting state is the finished state.** Every animation lives in
 * globals.css behind `prefers-reduced-motion: no-preference`, and what the
 * server renders -- and what a reduced-motion viewer keeps -- is the end of
 * each little story, not the start of it. Same rule EditorMock follows, and
 * the reason none of these need to be client components.
 *
 * `aria-hidden` throughout: the <h3> and <p> beside each one already say what
 * the picture says, and nothing here carries meaning the words do not.
 */

function Art({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <svg
      className="landing-art landing-loop-art"
      viewBox="0 0 104 60"
      role="presentation"
      aria-hidden="true"
      focusable="false"
      data-art={label}
    >
      {children}
    </svg>
  );
}

/** 01 -- three postings, the filter settling on the one that fits. */
export function FindArt() {
  return (
    <Art label="find">
      <rect x="6" y="7" width="58" height="12" rx="2" fill="none" stroke="var(--rule)" />
      <rect
        x="6"
        y="24"
        width="58"
        height="12"
        rx="2"
        fill="var(--landing-art-tint)"
        stroke="var(--clay)"
      />
      <rect x="6" y="41" width="58" height="12" rx="2" fill="none" stroke="var(--rule)" />
      <line x1="12" y1="13" x2="40" y2="13" stroke="var(--rule)" strokeWidth="2" />
      <line x1="12" y1="30" x2="46" y2="30" stroke="var(--clay)" strokeWidth="2" />
      <line x1="12" y1="47" x2="36" y2="47" stroke="var(--rule)" strokeWidth="2" />
      <g className="landing-art-sweep">
        <circle cx="82" cy="13" r="7" fill="none" stroke="var(--clay)" strokeWidth="1.5" />
        <line
          x1="87"
          y1="18"
          x2="93"
          y2="24"
          stroke="var(--clay)"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </g>
    </Art>
  );
}

/** 02 -- the copy peeling off the master, the posting clipped to it. */
export function ForkArt() {
  return (
    <Art label="fork">
      <rect x="8" y="8" width="38" height="44" rx="3" fill="none" stroke="var(--rule)" />
      <line x1="15" y1="18" x2="39" y2="18" stroke="var(--rule)" strokeWidth="2" />
      <line x1="15" y1="26" x2="35" y2="26" stroke="var(--rule)" strokeWidth="2" />
      <line x1="15" y1="34" x2="39" y2="34" stroke="var(--rule)" strokeWidth="2" />
      <g className="landing-art-peel">
        <rect
          x="56"
          y="8"
          width="38"
          height="44"
          rx="3"
          fill="var(--landing-art-tint)"
          stroke="var(--clay)"
        />
        <line x1="63" y1="18" x2="87" y2="18" stroke="var(--clay)" strokeWidth="2" />
        <line x1="63" y1="26" x2="80" y2="26" stroke="var(--clay)" strokeWidth="2" />
        <rect x="63" y="36" width="24" height="9" rx="2" fill="var(--clay)" />
      </g>
    </Art>
  );
}

/** 03 -- the compiled PDF under the scan, its checks coming up in order. */
export function ShipArt() {
  return (
    <Art label="ship">
      <rect x="16" y="6" width="56" height="48" rx="3" fill="var(--sheet)" stroke="var(--rule)" />
      <g stroke="var(--clay)" strokeWidth="1.8" fill="none" strokeLinecap="round">
        <path className="landing-art-tick" d="M24 17l3 3 5-6" />
        <path
          className="landing-art-tick"
          style={{ animationDelay: "0.3s" }}
          d="M24 29l3 3 5-6"
        />
        <path
          className="landing-art-tick"
          style={{ animationDelay: "0.6s" }}
          d="M24 41l3 3 5-6"
        />
      </g>
      <line x1="38" y1="17" x2="64" y2="17" stroke="var(--rule)" strokeWidth="2" />
      <line x1="38" y1="29" x2="60" y2="29" stroke="var(--rule)" strokeWidth="2" />
      <line x1="38" y1="41" x2="64" y2="41" stroke="var(--rule)" strokeWidth="2" />
      <line
        className="landing-art-scan"
        x1="16"
        y1="10"
        x2="72"
        y2="10"
        stroke="var(--clay)"
        strokeWidth="1"
        opacity="0.55"
      />
      <path d="M78 30h12" stroke="var(--clay)" strokeWidth="1.5" fill="none" />
      <path
        d="M88 26l5 4-5 4"
        fill="none"
        stroke="var(--clay)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </Art>
  );
}

/** 04 -- what the round taught you, going back into the master. */
export function KeepArt() {
  return (
    <Art label="keep">
      <rect x="8" y="8" width="44" height="44" rx="3" fill="none" stroke="var(--rule)" />
      <line x1="15" y1="18" x2="45" y2="18" stroke="var(--rule)" strokeWidth="2" />
      <line x1="15" y1="26" x2="40" y2="26" stroke="var(--rule)" strokeWidth="2" />
      <line
        className="landing-art-grow"
        x1="15"
        y1="34"
        x2="45"
        y2="34"
        stroke="var(--clay)"
        strokeWidth="2"
      />
      {/* At rest the chip has landed, and this is what says so: without the
          stub joining it to the master it reads as a pill parked nearby
          rather than as something going back in. */}
      <path d="M52 28h10" stroke="var(--clay)" strokeWidth="1.5" fill="none" />
      <rect
        className="landing-art-fly"
        x="62"
        y="22"
        width="32"
        height="13"
        rx="3"
        fill="var(--landing-art-tint)"
        stroke="var(--clay)"
      />
    </Art>
  );
}
