/** The `/` menu in the chat composer.
 *
 *  A command is a canned prompt with a slug. Picking one fills the composer
 *  and stops there, exactly as the opener suggestions do: the user reads it,
 *  edits it, and sends it themselves. Nothing here submits anything.
 *
 *  The copy lives in `messages/*.json` and is resolved by the shell, as it is
 *  for every other label the chat panel takes. This module only knows how to
 *  match what has been typed against a list it is handed. */

export type ChatCommand = {
  /** The slug typed after the slash. Lowercase ASCII, no spaces. */
  key: string;
  label: string;
  description: string;
  /** What lands in the composer when this command is picked. */
  prompt: string;
  /** The prompt ends mid-sentence and waits for the user to paste or type the
   *  rest (a job description, a target role). Only used to mark the row. */
  needsInput?: boolean;
};

/** A draft that is nothing but a slash token. Letters and digits of any
 *  script, because the menu is also filtered by label in zh-TW and zh-CN.
 *
 *  Matching the *whole* draft rather than the word under the caret is
 *  deliberate. It keeps the menu out of the way of ordinary prose -- "and/or",
 *  a URL, a date, a Typst path -- none of which should raise a popup, and it
 *  means picking a command can replace the draft outright without having to
 *  splice around a caret. */
const COMMAND_TOKEN = /^\/([\p{L}\p{N}_-]*)$/u;

/** The term the user is filtering by, or null when the draft is not a lone
 *  slash token and the menu should stay shut. `"/"` on its own gives `""`,
 *  which lists everything. */
export function commandTerm(draft: string): string | null {
  const match = COMMAND_TOKEN.exec(draft);
  return match ? match[1].toLocaleLowerCase() : null;
}

/** The point at which a term is specific enough to be worth matching against
 *  prose. One character appears in almost every label, so matching those too
 *  would mean `/a` listing the whole menu -- which is to say not filtering. */
const TEXT_MATCH_MIN = 2;

/** Commands matching `term`, best first: slug prefixes, then slugs containing
 *  it, then anything whose label or description mentions it. Order within a
 *  band is the caller's order, so the list stays stable as the user types.
 *
 *  Label matching is what makes the menu findable without knowing the slugs:
 *  "/recruiter" reaches "Review it like a recruiter", and in Chinese the slugs
 *  are unguessable so the label is the only handle. */
export function filterCommands(commands: ChatCommand[], term: string): ChatCommand[] {
  const needle = term.toLocaleLowerCase().trim();
  if (!needle) return [...commands];
  const prefix: ChatCommand[] = [];
  const inKey: ChatCommand[] = [];
  const inText: ChatCommand[] = [];
  const wide = needle.length >= TEXT_MATCH_MIN;
  for (const command of commands) {
    const key = command.key.toLocaleLowerCase();
    if (key.startsWith(needle)) {
      prefix.push(command);
    } else if (!wide) {
      continue;
    } else if (key.includes(needle)) {
      inKey.push(command);
    } else if (
      command.label.toLocaleLowerCase().includes(needle) ||
      command.description.toLocaleLowerCase().includes(needle)
    ) {
      inText.push(command);
    }
  }
  return [...prefix, ...inKey, ...inText];
}

/** Keyboard move through the menu, wrapping at both ends. Returns 0 for an
 *  empty list so the caller never holds an index it cannot render. */
export function moveCommandIndex(index: number, length: number, delta: number): number {
  if (length <= 0) return 0;
  return (((index + delta) % length) + length) % length;
}
