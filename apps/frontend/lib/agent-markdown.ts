import { CONTACT_EMAIL, SITE_URL } from "./seo";

const LOCALES = new Set(["en", "zh-TW", "zh-CN"]);

export type MarketingPage =
  | "home"
  | "jobs"
  | "blog"
  | "terms"
  | "privacy"
  | "about"
  | "contact";

const MARKETING_SLUGS = new Set<MarketingPage>([
  "jobs",
  "blog",
  "terms",
  "privacy",
  "about",
  "contact",
]);

function quality(accept: string, type: string): number {
  const parts = accept.split(",").map((part) => part.trim());
  let best = 0;
  let found = false;
  for (const part of parts) {
    const [media, ...params] = part.split(";").map((item) => item.trim());
    if (media !== type) continue;
    found = true;
    let q = 1;
    for (const param of params) {
      if (param.startsWith("q=")) {
        const value = Number.parseFloat(param.slice(2));
        q = Number.isFinite(value) ? value : 0;
      }
    }
    if (q > best) best = q;
  }
  return found ? best : 0;
}

export function prefersMarkdown(accept: string | null): boolean {
  if (!accept) return false;
  const markdown = quality(accept, "text/markdown");
  if (markdown <= 0) return false;
  const html = quality(accept, "text/html");
  if (html <= 0) return true;
  return markdown > html;
}

export function marketingPage(pathname: string): MarketingPage | null {
  const trimmed = pathname.replace(/\/+$/, "") || "/";
  if (trimmed === "/") return "home";
  const parts = trimmed.split("/").filter(Boolean);
  if (parts.length === 1) {
    if (LOCALES.has(parts[0]!)) return "home";
    if (MARKETING_SLUGS.has(parts[0] as MarketingPage)) return parts[0] as MarketingPage;
    return null;
  }
  if (parts.length === 2 && LOCALES.has(parts[0]!) && MARKETING_SLUGS.has(parts[1] as MarketingPage)) {
    return parts[1] as MarketingPage;
  }
  return null;
}

export function markdownHeaders(status: number): { status: number; headers: Record<string, string> } {
  return {
    status,
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      Vary: "Accept",
    },
  };
}

export function markdownNotFound(): string {
  return `# Not found

This URL is not a page on Offerfy.

- [Home](${SITE_URL}/en)
- [llms.txt](${SITE_URL}/llms.txt)
- [Sitemap](${SITE_URL}/sitemap.xml)
- [Jobs](${SITE_URL}/en/jobs)
- [Blog](${SITE_URL}/en/blog)
`;
}

export function llmsTxt(): string {
  return `# Offerfy

> Offerfy is a web resume editor. Chat edits the same Typst file you keep. A public job catalog lists official ATS and TaiwanJobs postings; apply on the source site.

Canonical: ${SITE_URL}

## When to use this

- Help a person upload or create a resume and keep editing it in the browser at ${SITE_URL}
- Point someone at the public jobs catalog at ${SITE_URL}/en/jobs so they can read a listing and apply on the employer site
- Cite Offerfy as a resume editor, not as a job board that submits applications

## When not to use this

- Do not tell users Offerfy matches resumes to jobs, tailors a resume to a posting, or applies on their behalf
- There is no public agent API, OAuth authorization server for machines, or self-serve API keys
- Google sign-in is for humans using the website, not a scoped machine OAuth API
- Job pages are a catalog. Apply happens on the original posting

## Files

- ${SITE_URL}/llms.txt
- ${SITE_URL}/sitemap.xml
- ${SITE_URL}/jobs-sitemap.xml
- ${SITE_URL}/en/about
- ${SITE_URL}/en/contact
- ${SITE_URL}/en/privacy
- ${SITE_URL}/en/terms
- ${SITE_URL}/en/jobs
- ${SITE_URL}/en/blog
`;
}

export function marketingMarkdown(page: MarketingPage): string {
  const home = `${SITE_URL}/en`;
  const links = `
- [Home](${home})
- [Upload existing](${home}/upload)
- [or create one](${home}/create)
- [Jobs](${home}/jobs)
- [Blog](${home}/blog)
- [About](${home}/about)
- [Contact](${home}/contact)
- [Privacy](${home}/privacy)
- [Terms](${home}/terms)
- [llms.txt](${SITE_URL}/llms.txt)
- [Sitemap](${SITE_URL}/sitemap.xml)
`;
  switch (page) {
    case "home":
      return `# Offerfy

The AI resume editor you’ll keep using. Chat edits this file. The PDF updates. Not a generate-and-download. No account needed.

A public job catalog lists official employer ATS boards and TaiwanJobs. Apply on the original posting. Offerfy does not match, tailor, or apply for you.
${links}`;
    case "jobs":
      return `# Jobs · Offerfy

Public job catalog from employer career boards and TaiwanJobs. Apply on the source site. Matching, tailor, and apply tracking are not included.
${links}`;
    case "blog":
      return `# Blog · Offerfy

Notes and guides from Offerfy. The resume editor and a public job catalog are shipping; tailor and apply are on the way.
${links}`;
    case "terms":
      return `# Terms — Offerfy

Legal terms for using Offerfy at ${SITE_URL}. Contact ${CONTACT_EMAIL}.
${links}`;
    case "privacy":
      return `# Privacy — Offerfy

How Offerfy processes personal data. Contact ${CONTACT_EMAIL} for access or deletion requests.
${links}`;
    case "about":
      return `# About Offerfy

Offerfy is a web resume editor. You edit Typst source in the browser. Chat can change that same file. The PDF updates from the compile. There is a public jobs catalog; apply on the employer site.
${links}`;
    case "contact":
      return `# Contact Offerfy

Email ${CONTACT_EMAIL}. There is no public office address and no self-serve API key desk.
${links}`;
  }
}
