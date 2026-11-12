"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PreferenceEditor from "@/components/jobs/PreferenceEditor";
import { useRouter } from "@/i18n/navigation";
import { ApiError, getMe } from "@/lib/api";
import { fetchJobPreferences, type JobPreference } from "@/lib/jobs";

export default function PreferencesPage() {
  const t = useTranslations("preferences");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [pref, setPref] = useState<JobPreference | null>(null);
  const [error, setError] = useState<string | null>(null);

  // One GET on load. The rebuild is a separate, explicit action in the editor.
  const load = useCallback(async () => {
    try {
      const me = await getMe();
      if (!me) {
        router.replace("/login");
        return;
      }
      setPref(await fetchJobPreferences());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("loadError"));
    }
  }, [router, t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <main className="mx-auto max-w-2xl px-5 py-12">
        <h1 className="rr-page-title mb-8">{t("title")}</h1>
        {error ? <p className="mb-4 text-sm text-destructive">{error}</p> : null}
        {!pref && !error ? (
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        ) : null}
        {pref ? <PreferenceEditor initial={pref} /> : null}
      </main>
      <Footer variant="app" />
    </div>
  );
}
