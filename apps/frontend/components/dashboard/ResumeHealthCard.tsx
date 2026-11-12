"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { refreshResumeFacts } from "@/lib/jobs";
import type { ResumeHealth } from "@/lib/dashboard";

type Props = {
  health: ResumeHealth;
  /** Called after a rebuild so the page can refetch the health it shows. */
  onRebuilt: () => void;
};

// A rebuild spends the person's credits, so it only ever runs from this
// button, never on a page visit.
export default function ResumeHealthCard({ health, onRebuilt }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function rebuild() {
    setBusy(true);
    setError(false);
    try {
      const res = await refreshResumeFacts();
      if (res.error) setError(true);
      onRebuilt();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  let body: React.ReactNode;
  if (!health.has_master) {
    body = (
      <>
        <p className="text-sm text-muted-foreground">{t("healthEmpty")}</p>
        <Link href="/new" className="rr-btn mt-3 inline-block">
          {t("newResume")}
        </Link>
      </>
    );
  } else {
    const reading =
      health.facts_state === "missing"
        ? t("healthFactsMissing")
        : health.facts_state === "stale"
          ? t("healthFactsStale")
          : health.needs_rebuild
            ? t("healthRebuildNeeded")
            : t("healthCurrent");
    body = (
      <>
        <p className="text-sm">{reading}</p>
        {/* Publishing is what a rebuild needs; offered only once it is true. */}
        {!health.published ? (
          <p className="mt-2 text-sm text-muted-foreground">{t("healthNotPublished")}</p>
        ) : null}
        {health.needs_rebuild && health.published ? (
          <button type="button" className="rr-btn mt-3" onClick={rebuild} disabled={busy}>
            {busy ? t("rebuilding") : t("rebuildNow")}
          </button>
        ) : null}
        {error ? (
          <p className="mt-2 text-sm text-destructive" role="alert">
            {tJobs("actionFailed")}
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section className="rr-card px-5 py-4" data-testid="dashboard-health" aria-labelledby="dash-health">
      <h2 id="dash-health" className="mb-2 text-sm font-semibold tracking-tight">
        {t("headingHealth")}
      </h2>
      {body}
    </section>
  );
}
