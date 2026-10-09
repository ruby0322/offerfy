"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export type TailorDraft = {
  title: string;
  target_label?: string;
  target_context?: string;
};

type Props = {
  defaultTitle: string;
  creating: boolean;
  error: string | null;
  onCreate: (draft: TailorDraft) => void;
  onClose: () => void;
  labels: {
    title: string;
    hint: string;
    name: string;
    targetLabel: string;
    targetContext: string;
    create: string;
    creating: string;
    cancel: string;
  };
};

export default function CreateTailorDialog({
  defaultTitle,
  creating,
  error,
  onCreate,
  onClose,
  labels,
}: Props) {
  const [name, setName] = useState(defaultTitle);
  const [targetLabel, setTargetLabel] = useState("");
  const [targetContext, setTargetContext] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
    nameRef.current?.select();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !creating) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [creating, onClose]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const title = name.trim();
    if (!title || creating) return;
    onCreate({
      title,
      target_label: targetLabel.trim() || undefined,
      target_context: targetContext.trim() || undefined,
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-foreground/20 p-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !creating) onClose();
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-tailor-title"
        data-testid="create-tailor-dialog"
        onSubmit={submit}
        className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-background p-5 shadow-lg"
      >
        <div>
          <h2 id="create-tailor-title" className="text-base font-semibold tracking-tight">
            {labels.title}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">{labels.hint}</p>
        </div>
        <label className="flex flex-col gap-1.5 text-sm">
          {labels.name}
          <Input
            ref={nameRef}
            name="title"
            value={name}
            maxLength={255}
            required
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          {labels.targetLabel}
          <Input
            name="target_label"
            value={targetLabel}
            maxLength={255}
            onChange={(event) => setTargetLabel(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          {labels.targetContext}
          <Textarea
            name="target_context"
            value={targetContext}
            rows={5}
            maxLength={8000}
            className="max-h-48"
            onChange={(event) => setTargetContext(event.target.value)}
          />
        </label>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" disabled={creating} onClick={onClose}>
            {labels.cancel}
          </Button>
          <Button type="submit" size="sm" disabled={creating || !name.trim()}>
            {creating ? labels.creating : labels.create}
          </Button>
        </div>
      </form>
    </div>
  );
}
