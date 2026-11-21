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
  assert.match(body, /self-serve MCP server/i);
  assert.match(body, /https:\/\/offerfy\.cc\/api\/mcp/);
  assert.match(body, /Authorization: Bearer ofa_/);
  assert.match(body, /no machine OAuth authorization server/i);
  assert.match(body, /Google sign-in is for humans/i);
  assert.match(body, /Job search queries the public catalogue/);
  assert.doesNotMatch(body, /own data: job search/i);
  assert.match(body, /No tool submits an application/);
  assert.doesNotMatch(body, /no public agent API/i);
  assert.doesNotMatch(body, /unauthenticated agent API/i);
  assert.doesNotMatch(body, /self-serve API keys/i);
  assert.match(body, /https:\/\/offerfy\.cc\/en\/about/);
});

test("home markdown has an H1 and recovery links", () => {
  const body = marketingMarkdown("home");
  assert.match(body, /^# /m);
  assert.match(body, /https:\/\/offerfy\.cc\/en\/upload/);
});

test("contact markdown points at settings, not a key desk", () => {
  const body = marketingMarkdown("contact");
  assert.match(body, /\/en\/settings/);
  assert.doesNotMatch(body, /no self-serve API key/i);
});

/** The fifteen tools the MCP surface exposes, as the page and llms.txt name them. */
const MCP_TOOLS = [
  "search_jobs",
  "read_job",
  "list_matches",
  "list_resumes",
  "get_resume",
  "create_resume",
  "create_tailor",
  "set_resume_title",
  "edit_resume",
  "put_resume_source",
  "publish_resume",
  "unpublish_resume",
  "export_resume",
  "get_job_preferences",
  "set_job_preferences",
];

test("the mcp page resolves in every path form", () => {
  for (const path of ["/mcp", "/mcp/", "/en/mcp", "/zh-TW/mcp", "/zh-CN/mcp"]) {
    assert.equal(marketingPage(path), "mcp", path);
  }
});

test("mcp markdown carries what an agent needs to connect itself", () => {
  const body = marketingMarkdown("mcp");
  assert.match(body, /https:\/\/offerfy\.cc\/api\/mcp(?![\w-])/);
  // And every mention of it is well formed, not just one. The page names the
  // endpoint three times -- the Endpoint: line, the command, the host's own
  // llms.txt section -- and a mutation check showed that breaking one of them
  // left the test green, because `match` is satisfied by any single occurrence.
  // A broken instruction is broken wherever it sits.
  assert.deepEqual(
    [...body.matchAll(/\/api\/mcp[\w-]+/g)].map((m) => m[0]),
    [],
    "an endpoint mention has something appended to it",
  );
  assert.match(body, /Authorization: Bearer/);
  // The point of this page for an agent reader: it can act on it.
  assert.match(body, /claude mcp add --transport http/);
});

test("mcp markdown names all fifteen tools", () => {
  const body = marketingMarkdown("mcp");
  for (const tool of MCP_TOOLS) {
    assert.ok(body.includes(tool), `missing ${tool}`);
  }
  assert.equal(MCP_TOOLS.length, 15);
});

test("mcp markdown keeps the standing denial: Offerfy does not apply for you", () => {
  // `llmsTxt` tells every agent not to say Offerfy submits applications. A page
  // about handing an agent the keys is exactly where that could drift.
  const body = marketingMarkdown("mcp");
  assert.doesNotMatch(body, /submits? (an )?application/i);
  assert.ok(!MCP_TOOLS.some((t) => /appl/i.test(t)), "no tool should imply applying");
});

test("llms.txt lists the mcp page among its files", () => {
  assert.match(llmsTxt(), /\/en\/mcp/);
});
