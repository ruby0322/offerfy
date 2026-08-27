import { getFormatter, getTranslations } from "next-intl/server";
import CtaRow from "@/components/blog/CtaRow";
import { renderPostBody } from "@/lib/blog/mdx";
import { displayDate, localeCopy } from "@/lib/blog/load";
import type { BlogPost } from "@/lib/blog/types";
import type { AppLocale } from "@/i18n/routing";

type Props = {
  post: BlogPost;
  locale: AppLocale;
};

export default async function PostArticle({ post, locale }: Props) {
  const t = await getTranslations("blog");
  const format = await getFormatter({ locale });
  const copy = localeCopy(post, locale);
  const dateValue = displayDate(post);
  const date = format.dateTime(new Date(`${dateValue}T00:00:00Z`), { dateStyle: "medium" });
  const body = await renderPostBody(post.slug, copy.body);
  const dateLabel = post.updatedAt ? t("updated", { date }) : date;

  return (
    <article className="blog-article">
      <p className="blog-meta">
        <span>{t(post.type)}</span>
        <span aria-hidden="true"> · </span>
        <time dateTime={dateValue}>{dateLabel}</time>
        {post.draft ? (
          <>
            <span aria-hidden="true"> · </span>
            <span>{t("draft")}</span>
          </>
        ) : null}
      </p>
      <h1 className="font-display">{copy.title}</h1>
      <p className="blog-dek">{copy.description}</p>
      <div className="blog-body">{body}</div>
      <CtaRow />
    </article>
  );
}
