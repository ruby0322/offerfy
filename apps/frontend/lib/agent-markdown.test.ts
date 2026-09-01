import assert from "node:assert/strict";
import test from "node:test";
import {
  llmsTxt,
  marketingMarkdown,
  marketingPage,
  markdownNotFound,
  prefersMarkdown,
} from "./agent-markdown";

test("prefers markdown only when it outranks HTML", () => {
  assert.equal(prefersMarkdown("text/markdown"), true);
  assert.equal(prefersMarkdown("text/markdown, text/html;q=0.8"), true);
  assert.equal(prefersMarkdown("text/html,application/xhtml+xml"), false);
  assert.equal(prefersMarkdown("text/html, text/markdown;q=0.5"), false);
  assert.equal(prefersMarkdown("*/*"), false);
  assert.equal(prefersMarkdown(null), false);
});

test("maps marketing paths and rejects job detail", () => {
  assert.equal(marketingPage("/"), "home");
  assert.equal(marketingPage("/en"), "home");
  assert.equal(marketingPage("/zh-TW/jobs"), "jobs");
  assert.equal(marketingPage("/en/about"), "about");
  assert.equal(marketingPage("/contact"), "contact");
  assert.equal(marketingPage("/en/jobs/ab359cd5-bec3-44c9-9b14-973fc9bb7e99"), null);
  assert.equal(marketingPage("/en/missing-on-purpose"), null);
});

test("404 markdown points agents at the sitemap and llms.txt", () => {
  const body = markdownNotFound();
  assert.match(body, /^# Not found/m);
  assert.match(body, /llms\.txt/);
  assert.match(body, /sitemap\.xml/);
  assert.match(body, /\/en\/jobs/);
});

test("llms.txt says when to use Offerfy and when not to", () => {
  const body = llmsTxt();
  assert.match(body, /When to use/);
  assert.match(body, /When not to use/);
  assert.match(body, /no public agent API/i);
  assert.match(body, /https:\/\/offerfy\.cc\/en\/about/);
});

test("home markdown has an H1 and recovery links", () => {
  const body = marketingMarkdown("home");
  assert.match(body, /^# /m);
  assert.match(body, /https:\/\/offerfy\.cc\/en\/upload/);
});
