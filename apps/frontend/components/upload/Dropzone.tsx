"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED_EXT = new Set(["pdf", "png", "jpg", "jpeg", "webp", "txt", "md"]);

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
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!ALLOWED_EXT.has(ext)) {
      setError(t("badType"));
      return;
    }
    if (file.size > MAX_BYTES) {
      setError(t("tooLarge"));
      return;
    }
    setError(null);
    onFile(file);
  }

  return (
    <div>
      <button
        type="button"
        className="rr-dropzone w-full"
        data-active={active ? "true" : "false"}
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setActive(true);
        }}
        onDragLeave={() => setActive(false)}
        onDrop={(event) => {
          event.preventDefault();
          setActive(false);
          accept(event.dataTransfer.files[0]);
        }}
      >
        <svg className="rr-drop-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 16V7m0 0-3.5 3.5M12 7l3.5 3.5M5 19h14"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <p className="text-base font-semibold tracking-tight">{t("drop")}</p>
        <p className="mt-2 text-sm text-muted-foreground">{t("hint")}</p>
      </button>
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,application/pdf,image/png,image/jpeg,image/webp,text/plain,text/markdown"
        onChange={(event) => accept(event.target.files?.[0])}
      />
      {error ? <p className="mt-3 text-sm text-destructive">{error}</p> : null}
    </div>
  );
}
