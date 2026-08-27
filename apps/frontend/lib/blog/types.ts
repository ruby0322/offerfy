import type { AppLocale } from "@/i18n/routing";

export const BLOG_LOCALES = ["en", "zh-TW", "zh-CN"] as const satisfies readonly AppLocale[];

export const MDX_COMPONENT_NAMES = ["Callout", "CtaRow", "Figure"] as const;

export type PostType = "guide" | "note";

export type PostMeta = {
  slug: string;
  type: PostType;
  publishedAt: string;
  updatedAt?: string;
  draft: boolean;
};

export type PostLocaleContent = {
  title: string;
  description: string;
  body: string;
};

export type BlogPost = PostMeta & {
  locales: Record<AppLocale, PostLocaleContent>;
};

export class BlogValidationError extends Error {
  constructor(public readonly errors: string[]) {
    super(errors.join("\n"));
    this.name = "BlogValidationError";
  }
}
