import { getFormatter, getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { displayDate, listPosts, localeCopy } from "@/lib/blog/load";
import type { AppLocale } from "@/i18n/routing";

type Props = {
  locale: AppLocale;
};

export default async function PostIndex({ locale }: Props) {
  const t = await getTranslations("blog");
  const format = await getFormatter({ locale });
  const posts = listPosts();

  return (
    <div className="blog-index mx-auto max-w-[72rem] px-5">
      <div className="blog-kicker" aria-hidden="true" />
      <h1 className="font-display">{t("title")}</h1>
      {posts.length === 0 ? (
        <p className="blog-empty">{t("empty")}</p>
      ) : (
        <ul className="blog-list">
          {posts.map((post) => {
            const copy = localeCopy(post, locale);
            const date = format.dateTime(new Date(`${displayDate(post)}T00:00:00Z`), {
              dateStyle: "medium",
            });
            return (
              <li key={post.slug}>
                <Link href={`/blog/${post.slug}`} className="blog-row">
                  <p className="blog-meta">
                    <span>{t(post.type)}</span>
                    <span aria-hidden="true"> · </span>
                    <time dateTime={displayDate(post)}>{date}</time>
                    {post.draft ? (
                      <>
                        <span aria-hidden="true"> · </span>
                        <span>{t("draft")}</span>
                      </>
                    ) : null}
                  </p>
                  <h2 className="font-display">{copy.title}</h2>
                  <p className="blog-dek">{copy.description}</p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
