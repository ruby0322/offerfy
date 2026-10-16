"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";

type Props = {
  title: string;
  description: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
  labels: {
    confirm: string;
    confirming: string;
    cancel: string;
  };
};

export default function ConfirmDeleteDialog({
  title,
  description,
  busy,
  error,
  onConfirm,
  onClose,
  labels,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
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
        aria-labelledby="confirm-delete-title"
        data-testid="confirm-delete-dialog"
        className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-background p-5 shadow-lg"
      >
        <div>
          <h2 id="confirm-delete-title" className="text-base font-semibold tracking-tight">
            {title}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={onClose}
          >
            {labels.cancel}
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            data-testid="confirm-delete-submit"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? labels.confirming : labels.confirm}
          </Button>
        </div>
      </div>
    </div>
  );
}
