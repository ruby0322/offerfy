import { SITE_URL } from "@/lib/seo";

export default function RootNotFound() {
  return (
    <main className="legal-article" style={{ padding: "2rem 1.25rem" }}>
      <h1>Not found</h1>
      <p>This URL is not a page on Offerfy.</p>
      <ul>
        <li>
          <a href="/en">Home</a>
        </li>
        <li>
          <a href="/llms.txt">llms.txt</a>
        </li>
        <li>
          <a href={`${SITE_URL}/sitemap.xml`}>Sitemap</a>
        </li>
        <li>
          <a href="/en/jobs">Jobs</a>
        </li>
        <li>
          <a href="/en/blog">Blog</a>
        </li>
      </ul>
    </main>
  );
}
