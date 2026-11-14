"use client";

import { useState, useTransition } from "react";
import { useTranslations } from "next-intl";
import type { AppLocale } from "@/i18n/routing";
import JobRow from "@/components/jobs/JobRow";
import {
  fetchJobListPage,
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
  const [seenInitial, setSeenInitial] = useState(initial);

  if (seenInitial !== initial) {
    setSeenInitial(initial);
    setItems(initial.items);
    setCursor(initial.next_cursor);
  }

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
      <ul className="jobs-rows">
        {items.map((job) => (
          <li key={job.id}>
            <JobRow job={job} locale={locale} />
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
