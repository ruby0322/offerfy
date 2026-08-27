import type { AppLocale } from "@/i18n/routing";
import { includeDrafts, listPublished, validateBlogTree } from "./validate";
import type { BlogPost } from "./types";

let loaded: BlogPost[] | null = null;

export function loadAllPosts(): BlogPost[] {
  if (process.env.NODE_ENV === "production") {
    loaded ??= validateBlogTree();
    return loaded;
  }
  return validateBlogTree();
}

export function listPosts(): BlogPost[] {
  return listPublished(loadAllPosts());
}

export function getPost(slug: string): BlogPost | null {
  return listPosts().find((post) => post.slug === slug) ?? null;
}

export function listSlugs(): string[] {
  return listPosts().map((post) => post.slug);
}

export function displayDate(post: BlogPost): string {
  return post.updatedAt ?? post.publishedAt;
}

export function localeCopy(post: BlogPost, locale: AppLocale) {
  return post.locales[locale];
}

export { includeDrafts };
