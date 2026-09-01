"use client";

import { useEffect, useState, useTransition } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import type { AppLocale } from "@/i18n/routing";
import {
  fetchJobListPage,
  sourceLabel,
  type JobList,
  type JobListItem,
  type JobQuery,
} from "@/lib/jobs";

type Props = {
  locale: AppLocale;
  query: JobQuery;
  initial: JobList;
};

export default function JobResults({ locale, query, initial }: Props) {
  const t = useTranslations("jobs");
  const [items, setItems] = useState<JobListItem[]>(initial.items);
  const [cursor, setCursor] = useState<string | null>(initial.next_cursor);
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    setItems(initial.items);
    setCursor(initial.next_cursor);
  }, [initial]);

  function loadMore() {
    if (!cursor || pending) return;
    const pageCursor = cursor;
    startTransition(async () => {
      const page = await fetchJobListPage(query, pageCursor);
      setItems((current) => {
        const seen = new Set(current.map((job) => job.id));
        return [...current, ...page.items.filter((job) => !seen.has(job.id))];
      });
      setCursor(page.next_cursor);
    });
  }

  return (
    <>
      <ul className="blog-list">
        {items.map((job) => (
          <li key={job.id}>
            <Link href={`/jobs/${job.id}`} className="blog-row">
              <p className="blog-meta">
                <span>{sourceLabel(job.source, locale)}</span>
                {job.company ? (
                  <>
                    <span aria-hidden="true"> · </span>
                    <span>{job.company}</span>
                  </>
                ) : null}
                {job.location ? (
                  <>
                    <span aria-hidden="true"> · </span>
                    <span>{job.location}</span>
                  </>
                ) : null}
                {job.remote ? (
                  <>
                    <span aria-hidden="true"> · </span>
                    <span>{t("remote")}</span>
                  </>
                ) : null}
              </p>
              <h2 className="font-display">{job.title}</h2>
            </Link>
          </li>
        ))}
      </ul>
      {cursor ? (
        <p className="jobs-more">
          <button type="button" className="jobs-more-btn" onClick={loadMore} disabled={pending}>
            {t("more")}
          </button>
        </p>
      ) : null}
    </>
  );
}
