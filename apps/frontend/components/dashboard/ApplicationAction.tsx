"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { listResumes, type Resume } from "@/lib/api";
import {
  confirmApplied,
  markApplied,
  patchApplication,
  type Application,
} from "@/lib/applications";
import ActionDialog, { type DialogChoice } from "./ActionDialog";
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
 * Anything with more than one outcome opens a dialog rather than expanding
 * in place. In a row the choices arrived as two or three bare verbs with the
 * posting they concerned a column away, and "一直沒有回覆" says nothing about
 * who never answered. The dialog names the employer and the role first, then
 * asks. See ./ActionDialog.
 *
 * Styling is the shared row hierarchy: the move that advances the search is
 * `rr-btn-accent`, bookkeeping is `rr-btn-ghost`, and nothing here is filled
 * -- the one filled button on a page is the next-action band.
 */

const PRIMARY = "rr-btn rr-btn-accent rr-btn-sm disabled:opacity-50";
const SECONDARY = "rr-btn rr-btn-ghost rr-btn-sm disabled:opacity-50";

/** Which question is open, if any. */
type Mode = null | "confirm" | "logResponse" | "applyDirect";

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
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  // Only fetched when the resume question is actually asked: every row on the
  // page would otherwise ask for the same list on mount.
  const [resumes, setResumes] = useState<Resume[] | null>(null);

  useEffect(() => {
    if (mode !== "applyDirect" || resumes !== null) return;
    let cancelled = false;
    listResumes()
      .then((items) => {
        if (!cancelled) setResumes(items);
      })
      .catch(() => {
        if (!cancelled) setResumes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, resumes]);

  async function run(op: () => Promise<Application>) {
    setBusy(true);
    setFailed(false);
    try {
      onChanged(await op());
      setMode(null);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  function control() {
    switch (nextAction(app)) {
      case "tailor":
        // Two ways on from a saved posting: tailor it, or send something you
        // already have. The second is not a lesser version of the first --
        // plenty of applications do not deserve a bespoke resume -- but it
        // stays the quieter button, because tailoring is what the product is.
        return (
          <>
            <Link className={PRIMARY} href={`/jobs/${encodeURIComponent(app.job_id)}`}>
              {t("actionTailor")}
            </Link>
            <button type="button" className={SECONDARY} onClick={() => setMode("applyDirect")}>
              {t("actionApplyDirect")}
            </button>
          </>
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
          <button type="button" className={SECONDARY} onClick={() => setMode("confirm")}>
            {t("didYouApply")}
          </button>
        );
      case "logResponse":
        return (
          <button type="button" className={SECONDARY} onClick={() => setMode("logResponse")}>
            {t("actionLogResponse")}
          </button>
        );
      default:
        return emptyLabel ? (
          <span className="text-xs text-muted-foreground">{emptyLabel}</span>
        ) : null;
    }
  }

  /** One short line per choice, where the wording alone leaves a doubt. */
  const RESPONSE_HINT: Record<string, string> = {
    responseHeardBack: "hintHeardBack",
    responseSaidNo: "hintSaidNo",
    responseNoAnswer: "hintNoAnswer",
  };

  function dialog() {
    if (mode === null) return null;
    const shared = {
      company: app.company,
      jobTitle: app.job_title,
      busy,
      error: failed ? tJobs("actionFailed") : null,
      cancelLabel: t("dialogCancel"),
      onClose: () => {
        if (busy) return;
        setMode(null);
        setFailed(false);
      },
    };

    if (mode === "confirm") {
      return (
        <ActionDialog
          {...shared}
          question={t("askConfirmApplied")}
          note={t("askConfirmAppliedNote")}
          choices={[
            { key: "yes", label: t("yes"), primary: true },
            { key: "no", label: t("no") },
          ]}
          onChoose={(key) => run(() => confirmApplied(app.job_id, key === "yes"))}
        />
      );
    }

    if (mode === "logResponse") {
      const choices: DialogChoice[] = responseChoices(app).map((c) => ({
        key: c.key,
        label: t(c.key),
        hint: RESPONSE_HINT[c.key] ? t(RESPONSE_HINT[c.key] as "hintHeardBack") : undefined,
        primary: c.status === "interviewing",
      }));
      return (
        <ActionDialog
          {...shared}
          question={t("askLogResponse")}
          note={t("askLogResponseNote")}
          choices={choices}
          onChoose={(key) => {
            const choice = responseChoices(app).find((c) => c.key === key);
            if (!choice) return;
            void run(() =>
              patchApplication(app.job_id, {
                status: choice.status,
                ...(choice.replied === undefined ? {} : { replied: choice.replied }),
              }),
            );
          }}
        />
      );
    }

    const loading = resumes === null;
    return (
      <ActionDialog
        {...shared}
        question={t("askWhichResume")}
        note={t("askWhichResumeNote")}
        choices={(resumes ?? []).map((resume) => ({
          key: resume.id,
          label: resume.title || resume.id,
          hint:
            (resume.kind ?? "master") === "tailor"
              ? resume.target_label || t("tailorBadge")
              : t("masterBadge"),
        }))}
        onChoose={(resumeId) => {
          // Opened here, synchronously inside the click, or a popup blocker
          // eats it: after an await the browser no longer calls this a
          // user gesture.
          window.open(app.apply_url, "_blank", "noopener,noreferrer");
          void run(() => markApplied(app.job_id, resumeId));
        }}
        onClose={shared.onClose}
      >
        {loading ? (
          <p className="text-sm text-muted-foreground">{t("loadingResumes")}</p>
        ) : resumes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("resumesEmpty")}</p>
        ) : undefined}
      </ActionDialog>
    );
  }

  return (
    <>
      <span className="inline-flex flex-col items-end gap-1">
        <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
          {control()}
        </span>
        {failed && mode === null ? (
          <span className="text-xs text-destructive" role="alert">
            {tJobs("actionFailed")}
          </span>
        ) : null}
      </span>
      {dialog()}
    </>
  );
}
