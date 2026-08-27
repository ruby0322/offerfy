---
name: publish-blog-post
description: Create, edit, translate, or publish Offerfy blog posts as git MDX. Use when the user asks to write a blog post, add a guide or note, translate a post, publish, unpublish, or update content/blog.
---

# Publish an Offerfy blog post

The operator does not edit posts in a CMS. You write files in git.

## Voice

- Indie landing tone. Short sentences. No SaaS template copy.
- Product name is **Offerfy** only.
- Never write CareerOS, Roleloop, Offerly, Offerloop, or RenderResume.
- ATS means parseability of the compiled PDF, not hireability, not a grade.
- Search, tailor, and apply are coming. Do not claim they ship.

## Files

One folder per slug:

```
apps/frontend/content/blog/<slug>/
  meta.yaml
  en.mdx
  zh-TW.mdx
  zh-CN.mdx
  images/          # optional
```

`meta.yaml`:

```yaml
type: guide | note
publishedAt: YYYY-MM-DD
updatedAt: YYYY-MM-DD   # optional; set when editing a published post
draft: true
```

Each locale MDX:

```mdx
---
title: "…"          # about 50–60 characters; this is the page H1
description: "…"    # about 140–160 characters; meta + Open Graph
---

## First heading in the body

Do not put an h1 in the body. No raw HTML. No `import`.
```

- Slug is kebab-case ASCII, stable, no dates. Folder name is the URL in every locale.
- All three locale files are always required, including drafts.
- Default source locale is `zh-TW`. Write that first, then real translations into `en` and `zh-CN` (not pasted English, not machine-identical copy).
- Ask for `type` (`guide` or `note`) if the user did not say.

## MDX whitelist

Only these JSX tags: `Callout`, `CtaRow`, `Figure`.

```mdx
<Callout label="Optional label">
Short aside.
</Callout>

<CtaRow />

<Figure src="filename.png" alt="Required description" />
```

`Figure` `src` is a filename in `images/` only. Prefer no images. Open Graph images are generated; do not add a PNG for OG.

The page layout already appends Create / Upload after the body. You may still use `CtaRow` in the body; you must not skip the files.

## Draft and publish

- New posts start `draft: true` unless the user says to publish now.
- Preview with `next dev` (drafts are visible locally). Production omits drafts.
- Publish: all three locales valid, then `draft: false`.
- Unpublish: `draft: true`, or delete the folder.

## SEO checklist

- Slug: readable English kebab-case, no stopword stuffing.
- Title ~50–60 characters; description ~140–160.
- Body starts at `##`. Real translations.
- No invented features, metrics, or testimonials.
- No keyword stuffing.

## Commands

From `apps/frontend`:

```bash
npm run blog:validate
```

Must pass before you call the post done. A missing locale or `<Tweet />` (or any unknown tag) fails the build.

## Git

Write the files. Run `blog:validate`. Commit only when the user asks. Do not push unless asked.
