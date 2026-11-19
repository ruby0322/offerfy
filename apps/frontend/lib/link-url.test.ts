import assert from "node:assert/strict";
import test from "node:test";

import { normalizeLinkUrl } from "./link-url";

const url = (u: string) => ({ kind: "url", url: u });

test("an allowed scheme passes through, scheme lower-cased", () => {
  assert.deepEqual(normalizeLinkUrl("https://example.com/a?b=1#c"), url("https://example.com/a?b=1#c"));
  assert.deepEqual(normalizeLinkUrl("http://example.com"), url("http://example.com"));
  assert.deepEqual(normalizeLinkUrl("HTTPS://Example.com/Path"), url("https://Example.com/Path"));
  assert.deepEqual(normalizeLinkUrl("mailto:me@example.com"), url("mailto:me@example.com"));
  assert.deepEqual(normalizeLinkUrl("MailTo:me@example.com"), url("mailto:me@example.com"));
});

test("surrounding whitespace is trimmed", () => {
  assert.deepEqual(normalizeLinkUrl("  https://example.com \n"), url("https://example.com"));
});

test("a url with no scheme becomes https", () => {
  assert.deepEqual(normalizeLinkUrl("example.com"), url("https://example.com"));
  assert.deepEqual(normalizeLinkUrl("www.example.com/cv"), url("https://www.example.com/cv"));
  assert.deepEqual(normalizeLinkUrl("github.com/me"), url("https://github.com/me"));
});

test("a host:port is not mistaken for a scheme", () => {
  assert.deepEqual(normalizeLinkUrl("example.com:8080/x"), url("https://example.com:8080/x"));
  assert.deepEqual(normalizeLinkUrl("localhost:3000"), url("https://localhost:3000"));
});

test("a host:port spelling cannot smuggle a scheme through", () => {
  // `javascript:123` looks like host:port, so it is prefixed -- which makes it an https
  // url for a host called "javascript", not a script.
  const r = normalizeLinkUrl("javascript:123");
  assert.deepEqual(r, url("https://javascript:123"));
  // ...and the same shape with a script body is not host:port at all.
  assert.equal(normalizeLinkUrl("javascript:1;alert(1)").kind, "rejected");
});

test("empty and blank mean remove the link", () => {
  assert.deepEqual(normalizeLinkUrl(""), { kind: "remove" });
  assert.deepEqual(normalizeLinkUrl("   \t "), { kind: "remove" });
});

test("hostile schemes are rejected, whatever the case or padding", () => {
  for (const hostile of [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  \t JaVaScRiPt:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "DATA:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com",
    "tel:+1234567",
    "blob:https://example.com/uuid",
    "about:blank",
  ]) {
    assert.deepEqual(
      normalizeLinkUrl(hostile),
      { kind: "rejected", reason: "scheme" },
      `${JSON.stringify(hostile)} must be refused`,
    );
  }
});

test("a scheme-relative or backslash url is rejected, not given an https prefix", () => {
  assert.deepEqual(normalizeLinkUrl("//evil.test"), { kind: "rejected", reason: "scheme" });
  assert.deepEqual(normalizeLinkUrl("  //evil.test/x"), { kind: "rejected", reason: "scheme" });
  assert.deepEqual(normalizeLinkUrl("\\\\evil.test"), { kind: "rejected", reason: "scheme" });
  assert.deepEqual(normalizeLinkUrl("/relative/path"), { kind: "rejected", reason: "scheme" });
});

test("control characters cannot split a scheme the browser would reassemble", () => {
  for (const sneaky of ["java\tscript:alert(1)", "java\nscript:alert(1)", "java\rscript:x", "\u0000javascript:x"]) {
    assert.equal(normalizeLinkUrl(sneaky).kind, "rejected", JSON.stringify(sneaky));
  }
  // An embedded space is not a url either: `#link("a b")` is a link nobody can follow.
  assert.deepEqual(normalizeLinkUrl("example.com/a b"), { kind: "rejected", reason: "invalid" });
});

test("an allowed scheme with no address is invalid", () => {
  for (const bad of ["https://", "https:///x", "https:example.com", "http://?q=1", "mailto:", "mailto:?subject=x", "?q=1", "#top", "@handle"]) {
    assert.deepEqual(normalizeLinkUrl(bad), { kind: "rejected", reason: "invalid" }, bad);
  }
});

test("an absurdly long url is invalid", () => {
  assert.deepEqual(normalizeLinkUrl(`https://example.com/${"a".repeat(2100)}`), {
    kind: "rejected",
    reason: "invalid",
  });
});

test("nothing it accepts can start with a non-allowed scheme", () => {
  // The property that matters, over a sweep: whatever comes back as `url` begins with an
  // allowed scheme.
  const probes = ["x", "a:b", "javascript://%0aalert(1)", "https://a", "mailto:a", "c:\\x", "1:2", "a.b:c"];
  for (const p of probes) {
    const r = normalizeLinkUrl(p);
    if (r.kind === "url") assert.match(r.url, /^(https?:\/\/|mailto:)/, p);
  }
});
