"use client";

import { useEffect, useRef, type ReactNode } from "react";
import EmployerLogo from "@/components/jobs/EmployerLogo";

/**
 * The dialog every multi-choice row action opens.
 *
 * Those actions used to expand in place: the row swapped its one button for
 * two or three unlabelled ones, which is the worst moment to lose context.
 * The question appears a row away from the posting it is about, in a table
 * where the row above is another posting, and the buttons had nothing but a
 * verb on them. "一直沒有回覆" on its own does not say *who* never answered.
 *
 * So a choice is a dialog, and the dialog says which posting it is about
 * before it asks anything: the employer's mark, the role, the company. The
 * chrome is ConfirmDeleteDialog's -- same overlay, same escape and
 * click-outside, same focus move -- because a second modal that behaves
 * differently from the first is a bug the reader has to discover.
 */

export type DialogChoice = {
  key: string;
  label: string;
  /** One short line under the label, where the choice needs explaining. */
  hint?: string;
  /** The consequential one, drawn as the row actions' accent. */
  primary?: boolean;
};

type Props = {
  company: string;
  jobTitle: string;
  /** What is being asked, in one sentence. */
  question: string;
  /** Optional sentence under the question; keep it to one line. */
  note?: string;
  choices: DialogChoice[];
  busy: boolean;
  error: string | null;
  cancelLabel: string;
  onChoose: (key: string) => void;
  onClose: () => void;
  /** Rendered in place of the choices while they are being fetched. */
  children?: ReactNode;
};

export default function ActionDialog({
  company,
  jobTitle,
  question,
  note,
  choices,
  busy,
  error,
  cancelLabel,
  onChoose,
  onClose,
  children,
}: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-foreground/20 p-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-dialog-question"
        data-testid="action-dialog"
        className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-background p-5 shadow-lg"
      >
        {/* Which posting, before what is being asked. */}
        <div className="flex min-w-0 items-center gap-2.5">
          <EmployerLogo company={company} size="sm" />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{jobTitle}</span>
            <span className="block truncate text-xs text-muted-foreground">{company}</span>
          </span>
        </div>

        <div>
          <h2
            id="action-dialog-question"
            className="text-base font-semibold tracking-tight"
          >
            {question}
          </h2>
          {note ? <p className="mt-1 text-xs text-muted-foreground">{note}</p> : null}
        </div>

        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        {children ?? (
          // One per line, full width: the labels are sentences of different
          // lengths, and a row of them wraps into a ragged block in Chinese.
          <div className="flex flex-col gap-2">
            {choices.map((choice) => (
              <button
                key={choice.key}
                type="button"
                data-testid="action-dialog-choice"
                disabled={busy}
                onClick={() => onChoose(choice.key)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-md border px-3 py-2.5 text-left disabled:opacity-50 ${
                  choice.primary
                    ? "border-[color-mix(in_srgb,var(--clay)_40%,var(--rule))] text-primary hover:bg-[color-mix(in_srgb,var(--clay)_8%,transparent)]"
                    : "border-rule hover:bg-hover"
                }`}
              >
                <span className="text-sm font-medium">{choice.label}</span>
                {choice.hint ? (
                  <span className="text-xs text-muted-foreground">{choice.hint}</span>
                ) : null}
              </button>
            ))}
          </div>
        )}

        <div className="flex justify-end">
          <button
            ref={closeRef}
            type="button"
            className="rr-btn rr-btn-ghost rr-btn-sm"
            disabled={busy}
            onClick={onClose}
          >
            {cancelLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
