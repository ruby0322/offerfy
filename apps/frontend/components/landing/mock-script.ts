/**
 * The hero mockup's animation, as data.
 *
 * The component is a dumb renderer: it asks `frameAt` what the mock should look
 * like at some number of milliseconds into the loop and draws exactly that. The
 * timeline lives here so it can be tested without a DOM, and so the order of
 * cause and effect is checkable rather than a thing you have to watch for.
 *
 * The order is the product's real chain: a person types, the reply streams, the
 * edit lands in the file, the file recompiles, and only then does the PDF
 * change. The source stays highlighted across the preview swap -- that lit line
 * is the only thing tying the two panes together, so it must outlive the swap.
 */

export type MockPhase =
  | "poster"
  | "ask"
  | "send"
  | "stream"
  | "diff"
  | "expand"
  | "file"
  | "compile"
  | "swap"
  | "hold";

/** Phases in the order the loop plays them. */
export const PHASES: MockPhase[] = [
  "poster",
  "ask",
  "send",
  "stream",
  "diff",
  "expand",
  "file",
  "compile",
  "swap",
  "hold",
];

// Per-token rates for the three phases that write text out.
export const TYPE_MS = 42;
export const STREAM_MS = 55;
export const WRITE_MS = 14;

// Fixed phase lengths.
export const POSTER_MS = 2600;
export const SEND_MS = 450;
export const DIFF_MS = 500;
export const EXPAND_MS = 900;
export const FILE_DWELL_MS = 700;
export const COMPILE_MS = 700;
export const SWAP_MS = 300;
export const HOLD_MS = 1800;

export type Step = { phase: MockPhase; ms: number };

/** Token counts for the three phases whose length depends on the copy. */
export type MockLengths = {
  promptChars: number;
  replyTokens: number;
  lineChars: number;
};

export type Stream = { tokens: string[]; sep: string };

/**
 * Split a reply into the units it streams in.
 *
 * English streams word by word, which is roughly what the real chat looks like
 * flushing deltas every 50ms. Chinese has no spaces, so splitting on them
 * yields one token and the whole reply would appear in a single tick; those
 * stream character by character instead. The 8x test holds because a
 * space-separated sentence averages ~6 characters per word, so words*8 always
 * clears its own length, while a CJK line never does.
 */
export function streamTokens(text: string): Stream {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length * 8 < text.length) {
    return { tokens: Array.from(text), sep: "" };
  }
  return { tokens: words, sep: " " };
}

export function buildScript(len: MockLengths): Step[] {
  return [
    { phase: "poster", ms: POSTER_MS },
    { phase: "ask", ms: Math.max(1, len.promptChars) * TYPE_MS },
    { phase: "send", ms: SEND_MS },
    { phase: "stream", ms: Math.max(1, len.replyTokens) * STREAM_MS },
    { phase: "diff", ms: DIFF_MS },
    { phase: "expand", ms: EXPAND_MS },
    { phase: "file", ms: Math.max(1, len.lineChars) * WRITE_MS + FILE_DWELL_MS },
    { phase: "compile", ms: COMPILE_MS },
    { phase: "swap", ms: SWAP_MS },
    { phase: "hold", ms: HOLD_MS },
  ];
}

export function scriptDuration(script: Step[]): number {
  return script.reduce((total, step) => total + step.ms, 0);
}

export type MockFrame = {
  phase: MockPhase;
  /** How many characters of the prompt are on screen. */
  promptChars: number;
  /** How many reply tokens are on screen. */
  replyTokens: number;
  /** How many characters of the new Typst line are on screen. */
  lineChars: number;
  /** The composer is being typed into. */
  composing: boolean;
  /** The "+1 -1" summary bar is up. */
  diffBar: boolean;
  /** The bar is expanded to the two diff lines. */
  diffOpen: boolean;
  /** Which tab the left pane is showing. */
  tab: "chat" | "typst";
  /** The edited source line is highlighted. */
  sweep: boolean;
  /** The preview is recompiling. */
  compiling: boolean;
  /** The preview shows the rewritten bullet. */
  updated: boolean;
};

function phaseAt(script: Step[], elapsed: number): { phase: MockPhase; progress: number; ms: number } {
  let start = 0;
  for (const step of script) {
    if (elapsed < start + step.ms) {
      return { phase: step.phase, progress: (elapsed - start) / step.ms, ms: step.ms };
    }
    start += step.ms;
  }
  const last = script[script.length - 1];
  return { phase: last.phase, progress: 1, ms: last.ms };
}

/**
 * What the mock looks like `elapsed` ms into the loop.
 *
 * `frameAt(0, …)` is the poster: the finished conversation next to the finished
 * PDF. That is what the server renders, what the first client paint shows, and
 * what a viewer who asked for reduced motion keeps -- so the claim the page
 * makes is legible without a single frame of movement.
 */
export function frameAt(elapsed: number, len: MockLengths): MockFrame {
  const script = buildScript(len);
  const total = scriptDuration(script);
  const at = elapsed <= 0 ? 0 : elapsed % total;
  const { phase, progress, ms } = phaseAt(script, at);

  const base: MockFrame = {
    phase,
    promptChars: len.promptChars,
    replyTokens: len.replyTokens,
    lineChars: len.lineChars,
    composing: false,
    diffBar: true,
    diffOpen: true,
    tab: "chat",
    sweep: false,
    compiling: false,
    updated: true,
  };

  switch (phase) {
    case "poster":
      return base;
    case "ask":
      return {
        ...base,
        // progress never reaches 1 inside its own phase, so scale by one
        // extra character: the last one lands in the final slice instead of
        // never at all, and `send` then holds the finished line.
        promptChars: Math.min(len.promptChars, Math.floor(progress * (len.promptChars + 1))),
        replyTokens: 0,
        lineChars: 0,
        composing: true,
        diffBar: false,
        diffOpen: false,
        updated: false,
      };
    case "send":
      return { ...base, replyTokens: 0, lineChars: 0, diffBar: false, diffOpen: false, updated: false };
    case "stream":
      return {
        ...base,
        replyTokens: Math.min(len.replyTokens, Math.ceil(progress * len.replyTokens)),
        lineChars: 0,
        diffBar: false,
        diffOpen: false,
        updated: false,
      };
    case "diff":
      return { ...base, lineChars: 0, diffOpen: false, updated: false };
    case "expand":
      return { ...base, lineChars: 0, updated: false };
    case "file": {
      const typing = Math.max(1, len.lineChars) * WRITE_MS;
      const t = progress * ms;
      return {
        ...base,
        tab: "typst",
        lineChars: Math.min(len.lineChars, Math.floor(t / WRITE_MS)),
        sweep: t >= typing,
        updated: false,
      };
    }
    case "compile":
      return { ...base, tab: "typst", sweep: true, compiling: true, updated: false };
    case "swap":
    case "hold":
      return { ...base, tab: "typst", sweep: true };
  }
}

export function sameFrame(a: MockFrame, b: MockFrame): boolean {
  return (
    a.phase === b.phase &&
    a.promptChars === b.promptChars &&
    a.replyTokens === b.replyTokens &&
    a.lineChars === b.lineChars &&
    a.composing === b.composing &&
    a.diffBar === b.diffBar &&
    a.diffOpen === b.diffOpen &&
    a.tab === b.tab &&
    a.sweep === b.sweep &&
    a.compiling === b.compiling &&
    a.updated === b.updated
  );
}

// The resume content the mock shows. It stays English in every locale: it is a
// Typst resume aimed at an English-language tech posting, and translating the
// bullets would make the preview a thing no user would ever produce.
export const BULLET_BEFORE =
  "worked on the resume editor and helped out with other projects";
export const BULLET_AFTER =
  "built a Typst resume editor with live PDF preview and chat that edits the same file";
export const BULLET_ATS = "added ATS checks on the compiled PDF — parseability, not hireability";

export const SOURCE_HEAD = `== Experience

#work(
  title: "Software Engineer",
  company: "Offerfy",
  dates: dates-helper(start-date: "Jan 2025", end-date: "Present"),
)`;

/** The line the edit writes into the source, as it appears in the Typst pane. */
export const SOURCE_LINE = `- ${BULLET_AFTER}`;
