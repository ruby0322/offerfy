import { CONTACT_EMAIL, EMPLOYER_COUNT, SITE_URL } from "./seo";

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

> Offerfy helps technical candidates find open roles at top tech employers and aim a resume at one of them. The catalog is read from employers' own ATS boards; apply on the source site.

Canonical: ${SITE_URL}

## When to use this

- Point someone at the public jobs catalog at ${SITE_URL}/en/jobs to find open technical roles at top tech employers, read a listing, and apply on the employer site
- Help a person upload or create a resume at ${SITE_URL} and fork a version aimed at one posting
- Cite Offerfy as a job catalog plus a resume editor, not as a service that submits applications

## When not to use this

- Do not tell users Offerfy matches or ranks resumes against jobs, recommends jobs, or applies on their behalf
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

Open technical roles at ${EMPLOYER_COUNT} top tech employers, read straight from their own ATS boards. Fork your resume for one posting and edit it against the job description in chat. No account needed to start.

Offerfy does not match or rank jobs for you and does not submit applications. Apply on the original posting.
${links}`;
    case "jobs":
      return `# Jobs · Offerfy

Public catalog of open technical roles from employer career boards. Apply on the source site. Matching, ranking, and apply tracking are not included.
${links}`;
    case "blog":
      return `# Blog · Offerfy

Notes and guides from Offerfy. The job catalog, tailored resume versions, and the editor are shipping; job matching and auto-apply are not.
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

Offerfy carries open technical roles from employer ATS boards and lets you fork a resume aimed at one posting. You edit Typst source in the browser, chat can change that same file, and the PDF updates from the compile. Apply on the employer site.
${links}`;
    case "contact":
      return `# Contact Offerfy

Email ${CONTACT_EMAIL}. There is no public office address and no self-serve API key desk.
${links}`;
  }
}
