"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Link } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import {
  refreshResumeFacts,
  saveJobPreferences,
  type JobPreference,
  type PreferenceActivity,
} from "@/lib/jobs";
import {
  COUNTRY_KEYS,
  FUNCTION_KEYS,
  SENIORITY_KEYS,
  type JobsMessageKey,
} from "@/components/jobs/JobSearchForm";

type Props = {
  initial: JobPreference;
  /** Re-reads the preference after a rebuild: the seed may have changed it. */
  onReload: () => Promise<void>;
};

const ACTIVITIES: PreferenceActivity[] = ["actively_looking", "browsing", "paused"];

// The select's value is a string; the wire wants true / false / null.
function remoteToValue(v: boolean | null): string {
  return v === null ? "" : v ? "true" : "false";
}
function valueToRemote(v: string): boolean | null {
  return v === "true" ? true : v === "false" ? false : null;
}

export default function PreferenceEditor({ initial, onReload }: Props) {
  const t = useTranslations("preferences");
  const tJobs = useTranslations("jobs");

  const [functions, setFunctions] = useState<string[]>(initial.target_functions);
  const [locations, setLocations] = useState<string[]>(initial.target_locations);
  const [seniority, setSeniority] = useState<string>(initial.target_seniority ?? "");
  const [remote, setRemote] = useState<boolean | null>(initial.remote_required);
  const [activity, setActivity] = useState<PreferenceActivity>(initial.activity);
  const [factsState, setFactsState] = useState(initial.facts_state);

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildMsg, setRebuildMsg] = useState<{ text: string; bad: boolean } | null>(null);

  // Same fallback as JobSearchForm: an unknown value shows itself rather than throws.
  function labeled(keys: Record<string, JobsMessageKey>, value: string): string {
    const key = keys[value];
    return key ? tJobs(key) : value;
  }

  function toggle(list: string[], set: (next: string[]) => void, value: string) {
    setSaved(false);
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  // Values the person already holds but the closed list lacks (the vocabulary
  // can grow ahead of this page) stay visible and removable instead of silently
  // surviving a save they cannot see.
  const functionValues = [
    ...Object.keys(FUNCTION_KEYS),
    ...functions.filter((v) => !(v in FUNCTION_KEYS)),
  ];
  const locationValues = [
    ...Object.keys(COUNTRY_KEYS).map((c) => c.toLowerCase()),
    ...locations.filter((v) => !(v.toUpperCase() in COUNTRY_KEYS)),
  ];
  const seniorityValues = [
    ...Object.keys(SENIORITY_KEYS),
    ...(seniority && !(seniority in SENIORITY_KEYS) ? [seniority] : []),
  ];

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const out = await saveJobPreferences({
        target_functions: functions,
        target_locations: locations,
        target_seniority: seniority || null,
        remote_required: remote,
        activity,
      });
      setFunctions(out.target_functions);
      setLocations(out.target_locations);
      setSeniority(out.target_seniority ?? "");
      setRemote(out.remote_required);
      setActivity(out.activity);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("saveError"));
    } finally {
      setSaving(false);
    }
  }

  async function rebuild() {
    setRebuilding(true);
    setRebuildMsg(null);
    try {
      const out = await refreshResumeFacts();
      setFactsState(out.facts_state);
      if (out.error) {
        setRebuildMsg({
          text: out.error === "no_source" ? t("rebuildNoSource") : t("rebuildFailed"),
          bad: true,
        });
      } else {
        setRebuildMsg({ text: t("rebuildDone"), bad: false });
        // Only the facts changed; the fields below are the person's and are
        // left exactly as they are, so no reload of their state here.
        await onReload();
      }
    } catch {
      setRebuildMsg({ text: t("rebuildFailed"), bad: true });
    } finally {
      setRebuilding(false);
    }
  }

  const selectClass = "rounded-md border border-input bg-background px-3 py-2 text-sm";

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("ownershipTitle")}</CardTitle>
          <CardDescription>{t("ownership")}</CardDescription>
        </CardHeader>
      </Card>

      {factsState !== "fresh" ? (
        <Card>
          <CardHeader>
            <CardTitle>
              {factsState === "stale" ? t("rebuildTitleStale") : t("rebuildTitleMissing")}
            </CardTitle>
            <CardDescription>{t("rebuildBody")}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" size="sm" disabled={rebuilding} onClick={() => void rebuild()}>
                {rebuilding ? t("rebuilding") : t("rebuild")}
              </Button>
              <Link href="/privacy" className="text-xs underline">
                {t("seePrivacy")}
              </Link>
            </div>
            {rebuildMsg ? (
              <p
                role="status"
                className={`text-xs ${rebuildMsg.bad ? "text-destructive" : "text-muted-foreground"}`}
              >
                {rebuildMsg.text}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="flex flex-col gap-5 pt-6">
          <fieldset className="flex flex-col gap-2 text-sm">
            <legend className="mb-1 font-medium">{t("functionsLegend")}</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {functionValues.map((value) => (
                <label key={value} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={functions.includes(value)}
                    onChange={() => toggle(functions, setFunctions, value)}
                  />
                  {labeled(FUNCTION_KEYS, value)}
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2 text-sm">
            <legend className="mb-1 font-medium">{t("locationsLegend")}</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {locationValues.map((value) => (
                <label key={value} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={locations.includes(value)}
                    onChange={() => toggle(locations, setLocations, value)}
                  />
                  {labeled(COUNTRY_KEYS, value.toUpperCase())}
                </label>
              ))}
            </div>
            <span className="text-xs text-muted-foreground">{t("locationsHint")}</span>
          </fieldset>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{tJobs("seniorityLabel")}</span>
            <select
              className={selectClass}
              value={seniority}
              onChange={(e) => {
                setSaved(false);
                setSeniority(e.target.value);
              }}
            >
              <option value="">{t("seniorityNone")}</option>
              {seniorityValues.map((value) => (
                <option key={value} value={value}>
                  {labeled(SENIORITY_KEYS, value)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{tJobs("remoteLabel")}</span>
            <select
              className={selectClass}
              value={remoteToValue(remote)}
              onChange={(e) => {
                setSaved(false);
                setRemote(valueToRemote(e.target.value));
              }}
            >
              <option value="">{t("remoteNone")}</option>
              <option value="true">{tJobs("remoteYes")}</option>
              <option value="false">{tJobs("remoteNo")}</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t("activityLabel")}</span>
            <select
              className={selectClass}
              value={activity}
              onChange={(e) => {
                setSaved(false);
                setActivity(e.target.value as PreferenceActivity);
              }}
            >
              {ACTIVITIES.map((value) => (
                <option key={value} value={value}>
                  {t(
                    value === "actively_looking"
                      ? "activityActive"
                      : value === "browsing"
                        ? "activityBrowsing"
                        : "activityPaused",
                  )}
                </option>
              ))}
            </select>
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" size="sm" disabled={saving} onClick={() => void save()}>
              {saving ? t("saving") : t("save")}
            </Button>
            {saved ? (
              <span role="status" className="text-xs text-muted-foreground">
                {t("saved")}
              </span>
            ) : null}
            {error ? <span className="text-xs text-destructive">{error}</span> : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
