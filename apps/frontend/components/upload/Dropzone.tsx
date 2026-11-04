"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ClipboardPaste, Upload } from "lucide-react";
import { ALLOWED_UPLOAD_EXTENSIONS, validateFile } from "@/lib/upload-intake";

// Deterministic because ALLOWED_UPLOAD_EXTENSIONS is a Set, which preserves
// insertion order -- this is the input's `accept` attribute, not a security
// boundary; `validateFile` is the actual gate and runs on every path.
const ACCEPT_EXTENSIONS = [...ALLOWED_UPLOAD_EXTENSIONS].map((ext) => `.${ext}`).join(",");
const ACCEPT_MIME =
  "application/pdf,image/png,image/jpeg,image/webp,text/plain,text/markdown";

type Props = {
  disabled?: boolean;
  onFile: (file: File) => void;
};

export default function Dropzone({ disabled, onFile }: Props) {
  const t = useTranslations("upload");
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function accept(file: File | undefined) {
    if (!file) return;
    const result = validateFile(file);
    if (!result.ok) {
      setError(t(result.reason));
      return;
    }
    setError(null);
    onFile(file);
  }

  return (
    <div>
      <div
        className="rr-dropzone w-full"
        data-active={active ? "true" : "false"}
        data-disabled={disabled ? "true" : "false"}
        onDragOver={(event) => {
          if (disabled) return;
          event.preventDefault();
          setActive(true);
        }}
        onDragLeave={() => setActive(false)}
        onDrop={(event) => {
          event.preventDefault();
          setActive(false);
          if (disabled) return;
          accept(event.dataTransfer.files[0]);
        }}
      >
        <p className="mb-3 text-xs text-muted-foreground">{t("dropHint")}</p>
        <div className="rr-doors flex flex-col items-stretch gap-3 sm:flex-row sm:gap-0">
          <button
            type="button"
            className="rr-door"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
          >
            <Upload className="rr-door-icon" aria-hidden="true" />
            <p className="text-base font-semibold tracking-tight">{t("chooseFile")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("chooseFileHint")}</p>
          </button>
          <div className="rr-door-divider" aria-hidden="true">
            <span>{t("or")}</span>
          </div>
          <div className="rr-door" data-static="true">
            <ClipboardPaste className="rr-door-icon" aria-hidden="true" />
            <p className="text-base font-semibold tracking-tight">{t("pasteDoor")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("pasteShortcut")}</p>
          </div>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept={`${ACCEPT_EXTENSIONS},${ACCEPT_MIME}`}
        onChange={(event) => accept(event.target.files?.[0])}
      />
      {error ? <p className="mt-3 text-sm text-destructive">{error}</p> : null}
    </div>
  );
}
