"use client";

import { FormEvent, useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  getMe,
  getResumeShare,
  putResumeShare,
  putResumeSource,
  type ImportStatus,
  type ResumeSource,
  type ShareState,
} from "@/lib/api";

type Props = {
  resumeId: string;
  title: string;
  createdAt?: string;
  source?: ResumeSource;
  resumeLocale?: string;
  importStatus?: ImportStatus;
  onTitleChange: (title: string) => void;
};

function shareUrl(token: string): string {
  return new URL(`/s/${token}`, window.location.origin).href;
}

export default function EditorSettingsPanel({
  resumeId,
  title,
  createdAt,
  source,
  resumeLocale,
  importStatus,
  onTitleChange,
}: Props) {
  const t = useTranslations("editor");
  const locale = useLocale();
  const [name, setName] = useState(title);
  const [prevTitle, setPrevTitle] = useState(title);
  if (title !== prevTitle) {
    setPrevTitle(title);
    setName(title);
  }
  const [saving, setSaving] = useState(false);
  const [nameStatus, setNameStatus] = useState("");
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [share, setShare] = useState<ShareState | null>(null);
  const [shareBusy, setShareBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (cancelled) return;
        const ok = me != null;
        setSignedIn(ok);
        if (!ok) return;
        try {
          const state = await getResumeShare(resumeId);
          if (!cancelled) setShare(state);
        } catch {
          if (!cancelled) setShare({ public: false, token: null });
        }
      } catch {
        if (!cancelled) setSignedIn(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [resumeId]);

  async function onSaveName(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameStatus(t("settingsNameError"));
      return;
    }
    setSaving(true);
    try {
      const saved = await putResumeSource(resumeId, { title: trimmed });
      const next = saved.title?.trim() || trimmed;
      setName(next);
      onTitleChange(next);
      setNameStatus(t("settingsSaved"));
    } catch (err) {
      setNameStatus(err instanceof ApiError ? err.message : t("settingsNameError"));
    } finally {
      setSaving(false);
    }
  }

  async function setPublic(isPublic: boolean) {
    setShareBusy(true);
    setCopied(false);
    try {
      const state = await putResumeShare(resumeId, isPublic);
      setShare(state);
    } catch {
      /* keep previous share state */
    } finally {
      setShareBusy(false);
    }
  }

  async function onCopy() {
    if (!share?.token) return;
    try {
      await navigator.clipboard.writeText(shareUrl(share.token));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const sourceLabel =
    source === "upload" ? t("settingsSourceUpload") : t("settingsSourceCreate");
  const importLabel =
    importStatus === "pending"
      ? t("settingsImportPending")
      : importStatus === "done"
        ? t("settingsImportDone")
        : importStatus === "failed"
          ? t("settingsImportFailed")
          : t("settingsImportIdle");
  const createdLabel = createdAt
    ? new Date(createdAt).toLocaleString(locale)
    : "—";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto flex max-w-lg flex-col gap-4">
        <Card>
          <CardHeader>
            <CardTitle>{t("settingsName")}</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="flex flex-col gap-3" onSubmit={onSaveName}>
              <input
                className="rr-input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={255}
                aria-label={t("settingsName")}
              />
              <div className="flex items-center gap-3">
                <Button type="submit" size="sm" disabled={saving}>
                  {t("settingsSave")}
                </Button>
                {nameStatus ? (
                  <span className="text-xs text-muted-foreground">{nameStatus}</span>
                ) : null}
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("settingsCreated")}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">{t("settingsCreated")}</dt>
              <dd>{createdLabel}</dd>
              <dt className="text-muted-foreground">{t("settingsSource")}</dt>
              <dd>{sourceLabel}</dd>
              <dt className="text-muted-foreground">{t("settingsLocale")}</dt>
              <dd>{resumeLocale || "—"}</dd>
              <dt className="text-muted-foreground">{t("settingsImport")}</dt>
              <dd>{importLabel}</dd>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("settingsShare")}</CardTitle>
            <CardDescription>
              {signedIn ? t("settingsShareHint") : t("settingsSignInHint")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {signedIn === false ? (
              <Link
                href={`/login?next=/editor/${resumeId}`}
                className="rr-btn inline-flex"
              >
                {t("settingsSignIn")}
              </Link>
            ) : null}
            {signedIn ? (
              <div className="flex flex-col gap-3">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={share?.public ? "outline" : "default"}
                    disabled={shareBusy}
                    onClick={() => setPublic(false)}
                  >
                    {t("settingsPrivate")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={share?.public ? "default" : "outline"}
                    disabled={shareBusy}
                    onClick={() => setPublic(true)}
                  >
                    {t("settingsPublic")}
                  </Button>
                </div>
                {share?.public && share.token ? (
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <input
                      className="rr-input min-w-0 flex-1"
                      readOnly
                      value={shareUrl(share.token)}
                      aria-label={t("settingsCopy")}
                    />
                    <Button type="button" size="sm" onClick={onCopy}>
                      {copied ? t("settingsCopied") : t("settingsCopy")}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
