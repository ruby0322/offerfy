"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import UsageCard from "@/components/UsageCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Link, useRouter } from "@/i18n/navigation";
import {
  ApiError,
  deleteTalentProfile,
  getConsents,
  getMe,
  getTalentProfile,
  listResumes,
  putConsent,
  putTalentProfileSource,
  refreshTalentProfile,
  type Resume,
  type TalentProfileStatus,
} from "@/lib/api";

export default function SettingsPage() {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const tLegal = useTranslations("legal");
  const router = useRouter();
  const [granted, setGranted] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<TalentProfileStatus | null>(null);
  const [masters, setMasters] = useState<Resume[]>([]);
  const [profileBusy, setProfileBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!me) {
          router.replace("/login");
          return;
        }
        const state = await getConsents();
        if (cancelled) return;
        setGranted(state.consents.talent_introduction ?? false);
        // Only when it is on: a profile is meaningless otherwise, and showing
        // the picker while matching is off invites building one nobody asked
        // us to build.
        if (state.consents.talent_introduction) await loadProfile();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : t("needLogin"));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  async function loadProfile() {
    const [status, resumes] = await Promise.all([getTalentProfile(), listResumes()]);
    setProfile(status);
    setMasters(resumes.filter((resume) => (resume.kind ?? "master") === "master"));
  }

  async function setMatching(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      const state = await putConsent("talent_introduction", next);
      const on = state.consents.talent_introduction ?? false;
      setGranted(on);
      // Turning it off deleted the profile server-side; drop it here too
      // rather than showing a card for something that no longer exists.
      if (on) await loadProfile();
      else setProfile(null);
    } catch {
      // Leave the displayed value alone: it still reflects the last answer
      // the ledger actually accepted.
      setError(t("error"));
    } finally {
      setBusy(false);
    }
  }

  async function runProfileAction(action: () => Promise<unknown>) {
    setProfileBusy(true);
    setError(null);
    try {
      await action();
      await loadProfile();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("profileError"));
      // Reload anyway: a refusal often changed nothing, and the status is
      // what tells them why.
      try {
        await loadProfile();
      } catch {
        /* the error above is the one worth showing */
      }
    } finally {
      setProfileBusy(false);
    }
  }

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-2xl px-5 py-12">
        <h1 className="rr-page-title mb-8">{t("title")}</h1>

        {error ? <p className="mb-4 text-sm text-destructive">{error}</p> : null}

        {granted == null && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}

        {granted != null ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("matchingTitle")}</CardTitle>
              {/* The label has to carry the notice itself -- "open to matching"
                  on its own does not tell anyone what leaves the product. */}
              <CardDescription>{t("matchingNotice")}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <p className="text-xs leading-5 text-muted-foreground">{t("matchingDefault")}</p>
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={granted ? "outline" : "default"}
                    disabled={busy}
                    onClick={() => void setMatching(false)}
                  >
                    {t("turnOff")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={granted ? "default" : "outline"}
                    disabled={busy}
                    onClick={() => void setMatching(true)}
                  >
                    {t("turnOn")}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {granted ? t("statusOn") : t("statusOff")}
                </p>
              </div>
              <Link href="/privacy" className="text-xs underline">
                {tLegal("seeAlsoPrivacy")}
              </Link>
            </CardContent>
          </Card>
        ) : null}

        {granted && profile ? (
          <Card className="mt-6">
            <CardHeader>
              <CardTitle>{t("profileTitle")}</CardTitle>
              <CardDescription>{t("profileNotice")}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <p className="text-xs leading-5 text-muted-foreground">{t("profileNoContact")}</p>

              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium">{t("profileSourceLabel")}</span>
                <select
                  className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={profile.source_resume_id ?? ""}
                  disabled={profileBusy}
                  onChange={(event) =>
                    void runProfileAction(() =>
                      putTalentProfileSource(event.target.value || null),
                    )
                  }
                >
                  <option value="">{t("profileSourceNone")}</option>
                  {masters.map((resume) => (
                    <option key={resume.id} value={resume.id}>
                      {resume.title || resume.id}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted-foreground">{t("profileSourceHint")}</span>
              </label>

              {profile.source_resume_id && !profile.has_published_revision ? (
                <p className="text-xs text-destructive">{t("profileNeedsPublish")}</p>
              ) : null}

              <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                {profile.profile ? (
                  <>
                    <span>
                      {profile.profile.headline ?? profile.profile.display_name ?? ""}
                    </span>
                    {profile.built_at ? (
                      <span>
                        {t("profileBuiltAt", {
                          date: new Date(profile.built_at).toLocaleDateString(),
                        })}
                      </span>
                    ) : null}
                    {profile.stale ? (
                      <span className="text-destructive">{t("profileStale")}</span>
                    ) : null}
                    {/* A disclosure record the person cannot see is a weaker
                        promise than one they can. */}
                    <span>
                      {profile.disclosure_count
                        ? t("profileDisclosures", { count: profile.disclosure_count })
                        : t("profileNoDisclosures")}
                    </span>
                  </>
                ) : (
                  <span>{t("profileNever")}</span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={profileBusy || !profile.has_published_revision}
                  onClick={() => void runProfileAction(refreshTalentProfile)}
                >
                  {profileBusy ? t("profileBuilding") : t("profileRefresh")}
                </Button>
                {profile.profile ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={profileBusy}
                    onClick={() => void runProfileAction(deleteTalentProfile)}
                  >
                    {t("profileDelete")}
                  </Button>
                ) : null}
              </div>
            </CardContent>
          </Card>
        ) : null}
        <UsageCard />
      </main>
      <Footer variant="app" />
    </div>
  );
}
