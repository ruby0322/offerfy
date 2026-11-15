/**
 * The four feature figures. Same rules as `loop-art.tsx`: the resting state is
 * the finished state, every animation is behind `prefers-reduced-motion`, and
 * the figure never carries a fact the body copy next to it does not state.
 *
 * **Every word in these drawings arrives as a prop.** SVG `<text>` is copy
 * like any other copy, and a label baked in here would be Traditional Chinese
 * on the English page -- the one failure mode a figure cannot recover from,
 * since there is no fallback to fall back to (AGENTS.md section 6). The ATS
 * names are the exception: Greenhouse and Workday are what they are called in
 * every locale.
 *
 * Label widths are the other constraint, and the tight one. These viewBoxes
 * are 212 units rendered into roughly 232 CSS pixels, so a unit is about a
 * pixel: a CJK glyph at `font-size="8"` eats 8 units and a Latin one about
 * 4.4, which is why the source pills are 80 units wide (SmartRecruiters is 15
 * characters) and why the context layers are tagged rather than described.
 * Lengthen one of these strings in a locale and it will run out of its box --
 * there is no wrapping in SVG text.
 */

import { EMPLOYER_MARKS } from "@/components/employer-marks";

function Figure({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <svg
      className="landing-art landing-feature-art"
      viewBox="0 0 212 128"
      role="img"
      aria-label={title}
      focusable="false"
    >
      {children}
    </svg>
  );
}

const SOURCES = ["Greenhouse", "Lever", "Ashby", "SmartRecruiters", "Workday"];
const SOURCE_Y = [8, 29, 50, 71, 92];

/**
 * Five boards converging into one list, with the row that is out of scope
 * dropping out of it. The dashed row is the one the scope rules refuse.
 */
export function IngestArt({ title }: { title: string }) {
  return (
    <Figure title={title}>
      {/* Nudged down so the drawing sits centred in the box. Its content runs
          y=8..107 of a 128-unit viewBox, which left twice as much air below it
          as above -- invisible at the old 264px, obvious once the figures grew
          to 344px. Shifting beats cropping the viewBox: all four figures keep
          the same aspect, so all four rows keep the same height. */}
      <g transform="translate(0, 6)">
      <g fontSize="8" fill="var(--muted)" fontFamily="var(--font-sans)">
        {SOURCES.map((name, index) => (
          <g key={name}>
            <rect
              x="4"
              y={SOURCE_Y[index]}
              width="80"
              height="15"
              rx="7.5"
              fill="none"
              stroke="var(--rule)"
            />
            <text x="44" y={SOURCE_Y[index] + 10.5} textAnchor="middle">
              {name}
            </text>
          </g>
        ))}
      </g>
      <path
        d="M86 15q28 0 30 42M86 36q24 0 28 21M86 57h28M86 78q24 0 28-21M86 99q28 0 30-42"
        fill="none"
        stroke="var(--rule)"
        strokeWidth="1"
      />
      <circle cx="116" cy="57" r="3" fill="var(--clay)" />
      <rect
        x="124"
        y="18"
        width="84"
        height="16"
        rx="2"
        fill="var(--landing-art-tint)"
        stroke="var(--clay)"
      />
      <line x1="131" y1="26" x2="188" y2="26" stroke="var(--clay)" strokeWidth="2" />
      <rect x="124" y="40" width="84" height="16" rx="2" fill="none" stroke="var(--rule)" />
      <line x1="131" y1="48" x2="180" y2="48" stroke="var(--rule)" strokeWidth="2" />
      <rect
        className="landing-art-drop"
        x="124"
        y="62"
        width="84"
        height="16"
        rx="2"
        fill="none"
        stroke="var(--rule)"
        strokeDasharray="3 3"
      />
      <rect x="124" y="84" width="84" height="16" rx="2" fill="none" stroke="var(--rule)" />
      <line x1="131" y1="92" x2="194" y2="92" stroke="var(--rule)" strokeWidth="2" />
      </g>
    </Figure>
  );
}

/**
 * Three of the week's matches, drawn the way the dashboard draws them.
 *
 * Same four slots in the same order as `dashboard/MatchRow.tsx` -- mark, name,
 * score -- minus the action button, so the figure is a smaller version of the
 * real row rather than an invented one. The score is `Math.round(fit_score *
 * 100)`, which is what that component renders, and the two above 80 take
 * --success because 0.8 is its STRONG threshold. Change the threshold there
 * and this figure is quietly telling a different story.
 *
 * The titles are English in every locale on purpose: these postings come off
 * Greenhouse and Ashby at US employers, so an English title is what a reader
 * in Taipei actually sees in the product. The scores are illustrative -- the
 * shape of a good week, not a measured average.
 */
const MATCHES = [
  { company: "Stripe", title: "Backend Engineer", score: 94 },
  { company: "OpenAI", title: "Infrastructure Engineer", score: 86 },
  { company: "NVIDIA", title: "ML Platform Engineer", score: 71 },
];
const STRONG = 80;

export function DigestArt({ title }: { title: string }) {
  return (
    <Figure title={title}>
      {MATCHES.map((match, index) => {
        const mark = EMPLOYER_MARKS[match.company];
        const y = 10 + index * 38;
        return (
          <g key={match.company}>
            <rect x="8" y={y + 7} width="20" height="20" rx="4" fill="none" stroke="var(--rule)" />
            {mark ? (
              <g transform={`translate(11.5, ${y + 10.5}) scale(0.5417)`}>
                <path d={mark.d} fill="var(--ink)" fillRule={mark.fillRule} />
              </g>
            ) : null}
            <text
              x="36"
              y={y + 17}
              fontSize="8"
              fill="var(--ink)"
              fontFamily="var(--font-sans)"
              dominantBaseline="central"
            >
              {match.title}
            </text>
            <text
              x="204"
              y={y + 17}
              fontSize="13"
              textAnchor="end"
              fill={match.score >= STRONG ? "var(--success)" : "var(--ink)"}
              fontFamily="var(--font-sans)"
              dominantBaseline="central"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {match.score}
            </text>
            {index < MATCHES.length - 1 ? (
              <line x1="8" y1={y + 34} x2="204" y2={y + 34} stroke="var(--rule)" />
            ) : null}
          </g>
        );
      })}
    </Figure>
  );
}

/**
 * The context a turn is assembled from, and the tools it has to act with.
 *
 * Those are the two things the section claims, so they are the two things
 * drawn: the stack on the left is what gets rebuilt every turn (the two clay
 * layers are what a tailor adds over a plain chat), and the row underneath is
 * the tool surface -- `read_job`, the Typst editor, the ATS pass and
 * `propose_memory`, four of the eight the editor actually exposes.
 *
 * The document still shows a diff, because that is what an edit looks like
 * coming back. It is no longer labelled or called out: one instance of it is
 * a drawing, three were a sales pitch.
 */
export function AgentArt({
  title,
  layers,
  diffOld,
  diffNew,
  tools,
}: {
  title: string;
  layers: readonly string[];
  diffOld: string;
  diffNew: string;
  tools: readonly string[];
}) {
  return (
    <Figure title={title}>
      <g fontSize="8" fontFamily="var(--font-sans)">
        {layers.map((layer, index) => (
          <g key={layer} className="landing-art-layer" style={{ animationDelay: `${index * 0.2}s` }}>
            <rect
              x="4"
              y={10 + index * 18}
              width="68"
              height="14"
              rx="2"
              fill={index < 2 ? "var(--landing-art-tint)" : "none"}
              stroke={index < 2 ? "var(--clay)" : "var(--rule)"}
            />
            <text x="12" y={20 + index * 18} fill={index < 2 ? "var(--clay)" : "var(--muted)"}>
              {layer}
            </text>
          </g>
        ))}
      </g>
      <path d="M76 44h12" stroke="var(--clay)" strokeWidth="1" fill="none" />
      <rect x="92" y="10" width="114" height="68" rx="3" fill="var(--sheet)" stroke="var(--rule)" />
      <g fontSize="9" fontFamily="var(--font-mono)">
        <text className="landing-art-fade" x="100" y="28" fill="var(--muted)">
          {diffOld}
        </text>
        <text x="100" y="46" fill="var(--clay)">
          {diffNew}
        </text>
      </g>
      <line x1="100" y1="60" x2="190" y2="60" stroke="var(--rule)" strokeWidth="2" />
      <line x1="100" y1="70" x2="168" y2="70" stroke="var(--rule)" strokeWidth="2" />
      {tools.map((tool, index) => (
        <g key={tool} className="landing-art-layer" style={{ animationDelay: `${index * 0.15}s` }}>
          <rect
            x={4 + index * 51}
            y="94"
            width="49"
            height="18"
            rx="4"
            fill="none"
            stroke="var(--rule)"
          />
          <text
            x={28.5 + index * 51}
            y="103"
            fontSize="7"
            fill="var(--muted)"
            fontFamily="var(--font-sans)"
            textAnchor="middle"
            dominantBaseline="central"
          >
            {tool}
          </text>
        </g>
      ))}
    </Figure>
  );
}

/**
 * Where a batch of applications actually ends up.
 *
 * A fan-in to a fork rather than four columns of cards: the columns drew the
 * states, and the states are not the point of a section titled "用結果說話".
 * The point is the proportions, so the three outgoing bands are weighted
 * 2 : 4 : 6 and the thickest of them is the one nobody answered -- the
 * outcome a pipeline view hides, and the reason `replied_at` exists as its
 * own column (see services/jobs/applications.py). Nothing here is a measured
 * average; it is the shape of a job search, not a statistic we are claiming.
 *
 * The marks are the self-hosted Simple Icons paths the employer wall already
 * uses, drawn in --ink rather than in brand colour. Brand colour would put
 * four more hues on a two-colour page, and OpenAI's own hex is #000000, which
 * disappears against the dark theme's sheet.
 */
const LOGOS = ["Stripe", "OpenAI", "NVIDIA", "Figma"];

export function TrackingArt({
  title,
  interview,
  replied,
  silent,
}: {
  title: string;
  interview: string;
  replied: string;
  silent: string;
}) {
  return (
    <Figure title={title}>
      {LOGOS.map((name, index) => {
        const mark = EMPLOYER_MARKS[name];
        const y = 8 + index * 28;
        return (
          <g key={name}>
            <rect x="4" y={y} width="22" height="22" rx="4" fill="none" stroke="var(--rule)" />
            {mark ? (
              <g transform={`translate(8, ${y + 4}) scale(0.5833)`}>
                <path d={mark.d} fill="var(--ink)" fillRule={mark.fillRule} />
              </g>
            ) : null}
            <path
              d={`M30 ${y + 11} Q48 ${y + 11} 58 64`}
              fill="none"
              stroke="var(--rule)"
              strokeWidth="1"
            />
          </g>
        );
      })}
      <circle cx="60" cy="64" r="3" fill="var(--clay)" />
      <path d="M64 64H88" stroke="var(--clay)" strokeWidth="12" opacity="0.18" fill="none" />
      <path
        d="M88 64Q118 64 146 26"
        fill="none"
        stroke="var(--clay)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M88 64H146"
        fill="none"
        stroke="var(--clay)"
        strokeWidth="4"
        opacity="0.55"
        strokeLinecap="round"
      />
      <path
        className="landing-art-pulse"
        d="M88 64Q118 64 146 102"
        fill="none"
        stroke="var(--warn)"
        strokeWidth="6"
        strokeLinecap="round"
      />
      <g fontSize="8" fontFamily="var(--font-sans)">
        <text x="152" y="29" fill="var(--clay)">
          {interview}
        </text>
        <text x="152" y="67" fill="var(--muted)">
          {replied}
        </text>
        <text x="152" y="105" fill="var(--warn)">
          {silent}
        </text>
      </g>
    </Figure>
  );
}
