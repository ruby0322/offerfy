import { compileMDX } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import { mdxComponents } from "@/components/blog/mdx-map";
import { remarkBlogRules } from "./remark-rules";

export async function renderPostBody(slug: string, body: string) {
  const { content } = await compileMDX({
    source: body,
    options: {
      mdxOptions: {
        remarkPlugins: [remarkGfm, remarkBlogRules],
      },
    },
    components: mdxComponents(slug),
  });
  return content;
}
