"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, getConsents, getMe, putConsent } from "@/lib/api";

export default function SettingsPage() {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const tLegal = useTranslations("legal");
  const router = useRouter();
  const [granted, setGranted] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        if (!cancelled) setGranted(state.consents.talent_introduction ?? false);
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

  async function setMatching(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      const state = await putConsent("talent_introduction", next);
      setGranted(state.consents.talent_introduction ?? false);
    } catch {
      // Leave the displayed value alone: it still reflects the last answer
      // the ledger actually accepted.
      setError(t("error"));
    } finally {
      setBusy(false);
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
      </main>
      <Footer variant="app" />
    </div>
  );
}
