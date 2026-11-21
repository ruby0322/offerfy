"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

/**
 * The one thing on the page that has to work.
 *
 * It is a prompt, not a description: the reader pastes it at their own agent and
 * the agent does the connecting. So it ends by telling the agent to list the
 * tools and report back — the reader gets proof it worked without having to
 * read the rest of the page.
 *
 * The clipboard write can fail (no permission, no secure context, a browser that
 * does not have the API), and a copy button that silently does nothing is worse
 * than one that says so. On failure the textarea stays selectable, which is the
 * fallback every browser has.
 */
export default function ConnectPrompt({ body }: { body: string }) {
  const t = useTranslations("mcp");
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(body);
      setState("copied");
      window.setTimeout(() => setState("idle"), 2000);
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("promptHelp")}</span>
        <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
          {state === "copied" ? t("copied") : t("copy")}
        </Button>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted px-3 py-2.5 font-mono text-xs leading-5">
        {body}
      </pre>
      {state === "failed" ? (
        <p className="text-xs text-muted-foreground">{t("copyFailed")}</p>
      ) : null}
    </div>
  );
}
