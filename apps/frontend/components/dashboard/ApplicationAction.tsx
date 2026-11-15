"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import {
  confirmApplied,
  markApplied,
  patchApplication,
  type Application,
} from "@/lib/applications";
import { nextAction, responseChoices } from "./applications";

/**
 * The one thing a row asks of the person, as a control.
 *
 * Lifted out of the status table so the overview's rows can carry the same
 * buttons rather than naming the action in plain text. Two copies would have
 * drifted on the first change -- the table already had three button styles
 * of its own before this -- and the transition rules are not something to
 * reimplement per surface: `nextAction` decides what to offer and
 * `responseChoices` decides what closing means, both in ./applications.
 *
 * Styling is the shared row hierarchy: the move that advances the search is
 * `rr-btn-accent`, bookkeeping is `rr-btn-ghost`, and nothing here is filled
 * -- the one filled button on a page is the next-action band.
 */

const PRIMARY = "rr-btn rr-btn-accent rr-btn-sm disabled:opacity-50";
const SECONDARY = "rr-btn rr-btn-ghost rr-btn-sm disabled:opacity-50";

type Props = {
  app: Application;
  /** Hands back the row the API returned, so the caller can replace it. */
  onChanged: (next: Application) => void;
  /** Shown when the row has nothing to ask (closed). */
  emptyLabel?: string;
};

export default function ApplicationAction({ app, onChanged, emptyLabel }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function run(op: () => Promise<Application>) {
    setBusy(true);
    setFailed(false);
    try {
      onChanged(await op());
      setChoosing(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  function control() {
    switch (nextAction(app)) {
      case "tailor":
        return (
          <Link className={PRIMARY} href={`/jobs/${encodeURIComponent(app.job_id)}`}>
            {t("actionTailor")}
          </Link>
        );
      case "apply":
        return (
          <a
            className={PRIMARY}
            href={app.apply_url}
            rel="noopener noreferrer"
            target="_blank"
            // Fire and forget: the posting must open whether or not this lands.
            onClick={() => {
              markApplied(app.job_id).then(onChanged).catch(() => {});
            }}
          >
            {t("actionApply")}
          </a>
        );
      case "confirm":
        return (
          <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
            <span className="text-xs text-muted-foreground">{t("didYouApply")}</span>
            <button
              type="button"
              className={SECONDARY}
              disabled={busy}
              onClick={() => run(() => confirmApplied(app.job_id, true))}
            >
              {t("yes")}
            </button>
            <button
              type="button"
              className={SECONDARY}
              disabled={busy}
              onClick={() => run(() => confirmApplied(app.job_id, false))}
            >
              {t("no")}
            </button>
          </span>
        );
      case "logResponse":
        // Closing offers two buttons that differ only in whether anyone
        // replied -- the one fact the status cannot carry once it is closed.
        return choosing ? (
          <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
            {responseChoices(app).map((c) => (
              <button
                key={c.key}
                type="button"
                className={SECONDARY}
                disabled={busy}
                onClick={() =>
                  run(() =>
                    patchApplication(app.job_id, {
                      status: c.status,
                      ...(c.replied === undefined ? {} : { replied: c.replied }),
                    }),
                  )
                }
              >
                {t(c.key)}
              </button>
            ))}
          </span>
        ) : (
          <button type="button" className={SECONDARY} onClick={() => setChoosing(true)}>
            {t("actionLogResponse")}
          </button>
        );
      default:
        return emptyLabel ? (
          <span className="text-xs text-muted-foreground">{emptyLabel}</span>
        ) : null;
    }
  }

  return (
    <span className="inline-flex flex-col items-end gap-1">
      {control()}
      {failed ? (
        <span className="text-xs text-destructive" role="alert">
          {tJobs("actionFailed")}
        </span>
      ) : null}
    </span>
  );
}
