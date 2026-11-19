/**
 * What the person typed into the link row, turned into a url a `link` mark may carry.
 *
 * The string ends up in `#link("...")` in their Typst source and as a clickable link in
 * the compiled PDF, where a `javascript:` or `data:` target is live in some viewers. So
 * this is an allow-list, not a block-list: `http`, `https` and `mailto` pass, a bare
 * `example.com` becomes `https://example.com` (what the `basic-resume` template does for
 * its own `url:`), and everything else is refused with a reason the UI can show.
 *
 * An empty string is its own answer, `remove`: `serialize_unit` raises on a blank link
 * url, so "a link with no url" is not something the person can have -- and "I cleared
 * the box" can only mean they no longer want the link.
 */
export type LinkUrlResult =
  | { kind: "remove" }
  | { kind: "url"; url: string }
  | { kind: "rejected"; reason: "scheme" | "invalid" };

const ALLOWED_SCHEMES = new Set(["http", "https", "mailto"]);
const MAX_LENGTH = 2048;

// C0 controls and DEL. Browsers strip tab/CR/LF *inside* a url before reading its scheme,
// so `java\tscript:` is `javascript:` to them; refusing every control character here
// means no such spelling survives to be normalised.
const CONTROL = /[\u0000-\u001f\u007f]/;
const WHITESPACE = /\s/u;
const SCHEME = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;
// `example.com:8080/x` reads as scheme `example.com`. A port is digits followed by the
// end or a path/query/fragment, which no scheme's body looks like.
const HOST_PORT = /^[^/?#:@]+:\d+(?:[/?#]|$)/;
// A host, then anything: at least one character that is not a delimiter.
const HTTP_BODY = /^\/\/[^/?#\s@]/;

export function normalizeLinkUrl(input: string): LinkUrlResult {
  const trimmed = input.trim();
  if (trimmed === "") return { kind: "remove" };
  if (trimmed.length > MAX_LENGTH || CONTROL.test(trimmed) || WHITESPACE.test(trimmed)) {
    return { kind: "rejected", reason: "invalid" };
  }
  // `//evil.test` is scheme-relative and `\\evil.test` is read as the same thing by
  // browsers: neither is a url this feature can vouch for, and prefixing them with
  // `https:` would aim the link at a host the person did not type.
  if (trimmed.startsWith("/") || trimmed.startsWith("\\")) {
    return { kind: "rejected", reason: "scheme" };
  }

  const match = SCHEME.exec(trimmed);
  if (match && !HOST_PORT.test(trimmed)) {
    const scheme = match[1].toLowerCase();
    if (!ALLOWED_SCHEMES.has(scheme)) return { kind: "rejected", reason: "scheme" };
    const rest = trimmed.slice(match[0].length);
    if (scheme === "mailto") {
      return rest === "" || rest.startsWith("?")
        ? { kind: "rejected", reason: "invalid" }
        : { kind: "url", url: `mailto:${rest}` };
    }
    // `https:example.com` and `https:///x` are not addresses a viewer will follow.
    if (!HTTP_BODY.test(rest)) return { kind: "rejected", reason: "invalid" };
    return { kind: "url", url: `${scheme}:${rest}` };
  }

  // No scheme: what people actually type. A leading `?`, `#` or `@` has no host to speak of.
  if (/^[?#@]/.test(trimmed)) return { kind: "rejected", reason: "invalid" };
  return { kind: "url", url: `https://${trimmed}` };
}
