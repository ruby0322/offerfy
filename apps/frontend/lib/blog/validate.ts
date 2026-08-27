import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import remarkFrontmatter from "remark-frontmatter";
import remarkGfm from "remark-gfm";
import remarkMdx from "remark-mdx";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { visit } from "unist-util-visit";
import yaml from "yaml";
import { routing, type AppLocale } from "@/i18n/routing";
import { blogRoot, imageExists, postDir } from "./paths";
import { remarkBlogRules } from "./remark-rules";
import {
  BLOG_LOCALES,
  BlogValidationError,
  type BlogPost,
  type PostLocaleContent,
  type PostMeta,
  type PostType,
} from "./types";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_RE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value);
}

function parseMeta(slug: string, raw: string): PostMeta {
  const data = yaml.parse(raw) as Record<string, unknown> | null;
  if (!data || typeof data !== "object") {
    throw new Error("meta.yaml is empty or invalid");
  }
  const type = data.type;
  if (type !== "guide" && type !== "note") {
    throw new Error(`type must be guide or note, got ${String(type)}`);
  }
  if (!isIsoDate(data.publishedAt)) {
    throw new Error(`publishedAt must be YYYY-MM-DD, got ${String(data.publishedAt)}`);
  }
  let updatedAt: string | undefined;
  if (data.updatedAt != null) {
    if (!isIsoDate(data.updatedAt)) {
      throw new Error(`updatedAt must be YYYY-MM-DD, got ${String(data.updatedAt)}`);
    }
    updatedAt = data.updatedAt;
  }
  if (typeof data.draft !== "boolean") {
    throw new Error("draft must be true or false");
  }
  return {
    slug,
    type: type as PostType,
    publishedAt: data.publishedAt,
    ...(updatedAt ? { updatedAt } : {}),
    draft: data.draft,
  };
}

function parseLocaleMdx(slug: string, locale: AppLocale, raw: string): PostLocaleContent {
  const parsed = matter(raw);
  const title = parsed.data.title;
  const description = parsed.data.description;
  if (typeof title !== "string" || !title.trim()) {
    throw new Error(`${locale}.mdx title is required`);
  }
  if (typeof description !== "string" || !description.trim()) {
    throw new Error(`${locale}.mdx description is required`);
  }

  const tree = unified().use(remarkParse).use(remarkFrontmatter).use(remarkGfm).use(remarkMdx).parse(parsed.content);

  remarkBlogRules()(tree);

  visit(tree, (node) => {
    if (node.type === "mdxJsxFlowElement" || node.type === "mdxJsxTextElement") {
      const jsx = node as { name?: string | null; attributes?: Array<{ type: string; name?: string; value?: unknown }> };
      if (jsx.name === "Figure") {
        const srcAttr = jsx.attributes?.find((item) => item.type === "mdxJsxAttribute" && item.name === "src");
        const src = typeof srcAttr?.value === "string" ? srcAttr.value : null;
        if (src && !imageExists(slug, src)) {
          throw new Error(`${locale}.mdx Figure src not found: images/${src}`);
        }
      }
    }
  });

  return { title: title.trim(), description: description.trim(), body: parsed.content };
}

function readRequired(filePath: string, label: string): string {
  if (!fs.existsSync(filePath)) {
    throw new Error(`missing ${label}`);
  }
  const raw = fs.readFileSync(filePath, "utf8");
  if (!raw.trim()) {
    throw new Error(`${label} is empty`);
  }
  return raw;
}

export function validateBlogTree(): BlogPost[] {
  assertLocales();
  const root = blogRoot();
  if (!fs.existsSync(root)) {
    return [];
  }

  const entries = fs.readdirSync(root, { withFileTypes: true });
  const errors: string[] = [];
  const posts: BlogPost[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const slug = entry.name;
    if (!SLUG_RE.test(slug)) {
      errors.push(`${slug}: slug must be kebab-case ASCII`);
      continue;
    }

    try {
      const dir = postDir(slug);
      const meta = parseMeta(slug, readRequired(path.join(dir, "meta.yaml"), "meta.yaml"));
      const locales = {} as Record<AppLocale, PostLocaleContent>;
      for (const locale of BLOG_LOCALES) {
        const raw = readRequired(path.join(dir, `${locale}.mdx`), `${locale}.mdx`);
        locales[locale] = parseLocaleMdx(slug, locale, raw);
      }
      posts.push({ ...meta, locales });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      errors.push(`${slug}: ${message}`);
    }
  }

  if (errors.length > 0) {
    throw new BlogValidationError(errors);
  }

  posts.sort((a, b) => {
    const byDate = b.publishedAt.localeCompare(a.publishedAt);
    return byDate !== 0 ? byDate : a.slug.localeCompare(b.slug);
  });
  return posts;
}

export function includeDrafts(): boolean {
  return process.env.NODE_ENV !== "production";
}

export function listPublished(posts: BlogPost[]): BlogPost[] {
  return includeDrafts() ? posts : posts.filter((post) => !post.draft);
}

/** Ensure routing locales stay aligned with blog files. */
export function assertLocales(): void {
  for (const locale of routing.locales) {
    if (!BLOG_LOCALES.includes(locale)) {
      throw new Error(`routing locale ${locale} is not a blog locale`);
    }
  }
}
