import Link from "next/link";
import { SITE_URL } from "@/lib/seo";

export default function RootNotFound() {
  return (
    <main className="legal-article" style={{ padding: "2rem 1.25rem" }}>
      <h1>Not found</h1>
      <p>This URL is not a page on Offerfy.</p>
      <ul>
        <li>
          <Link href="/en">Home</Link>
        </li>
        <li>
          <a href="/llms.txt">llms.txt</a>
        </li>
        <li>
          <a href={`${SITE_URL}/sitemap.xml`}>Sitemap</a>
        </li>
        <li>
          <Link href="/en/jobs">Jobs</Link>
        </li>
        <li>
          <Link href="/en/blog">Blog</Link>
        </li>
      </ul>
    </main>
  );
}
