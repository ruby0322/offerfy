"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  COUNTRY_KEYS,
  FUNCTION_KEYS,
  SENIORITY_KEYS,
  type JobsMessageKey,
} from "@/components/jobs/facet-labels";
import { Link } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import {
  fetchJobPreferences,
  fetchMatchProgress,
  refreshResumeFacts,
  saveJobPreferences,
  startFirstRun,
  type JobPreference,
} from "@/lib/jobs";
import {
  POLL_MS,
  addValue,
  canSave,
  firstRunNotice,
  needsFactsBuild,
  pollVerdict,
  removeValue,
  type OnboardNotice,
} from "./onboarding";

/**
 * The one blocking step, as three questions that are already answered.
 *
 * It used to be a text link inside a pill that opened a wall of unticked
 * boxes: eight functions, every country, two selects and an activity picker.
 * Here the resume's seed is pre-filled (`pref` is the computed seed until the
 * first save), so the card is a confirmation rather than a form, and one
 * button saves and starts scoring while the person is still looking.
 *
 * The three questions are the axes `prefilter_query` actually filters on --
 * function, location, seniority -- because a question that changes no filter
 * changes no result. `remote_required` is a fourth real axis and is left to
 * the full editor on purpose: setting it makes the countries inert, a
 * surprise that wants the editor's explanation, not a radio button in the
 * first minute.
 *
 * Built for zh-TW first: nothing has a fixed width. Chips and the select wrap
 * on their own line, the questions stack in one column rather than three
 * cramped ones, and the button is as wide as its label.
 *
 * While scoring there is an indeterminate spinner and a sentence, never a bar:
 * the server commits once, so progress reads 0 until the pass is over and then
 * jumps to the final count. Polling is bounded here (see `pollVerdict`)
 * because the server has no "running" answer to stop on.
 *
 * THE CARD BUILDS THE FACTS IT NEEDS. The seed comes from `ResumeFacts`, and
 * for a brand-new account nothing has built that row: the three callers of
 * `build_facts` are the Rebuild button, the weekly digest and the sweep, and
 * the sweep skips anyone with no SAVED preference, which is precisely the
 * person this card is for. So the card read "We read your resume and filled
 * these in" over no chips and a disabled Save -- the first screen of the
 * feature, with a false sentence on it.
 *
 * So when the facts are `missing` the card asks for them itself, through the
 * same `POST /v1/me/resume-facts/refresh` the Rebuild button uses, and then
 * re-reads the preference seed. That endpoint is metered through `preflight`
 * with purpose `profile` -- an asked-for action, which slice 1 unblocked for
 * the registered tier precisely so a person could do this, and someone
 * opening this card is asking. The anti-goal about not spending a person's
 * allowance is about MATCHING spend the platform decided to do; the first run
 * itself still goes through `system_guard` for exactly that reason.
 *
 * A 429 or an extraction fault leaves the three questions answerable by hand,
 * and the lead sentence is conditional on whether a seed actually resulted --
 * it must never claim a resume was read when it was not.
 */

type Props = {
  /** The seed, before a first save. */
  pref: JobPreference;
  /** Reloads the dashboard. Must reject when it fails, so the card can say so. */
  onDone: () => Promise<void>;
};

type Phase =
  | { kind: "edit" }
  // Building the resume facts the seed comes from. Mount-time only, and only
  // when there are none; it is a provider call, so it is made once.
  | { kind: "reading" }
  | { kind: "saving" }
  | { kind: "scoring" }
  // Saved, but scoring did not finish or did not start. The preference is
  // stored either way; the notice says what is true now.
  | { kind: "stopped"; notice: OnboardNotice | "onboardSlow" };

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export default function OnboardingCard({ pref, onDone }: Props) {
  const t = useTranslations("dashboard");
  const tJobs = useTranslations("jobs");

  const [functions, setFunctions] = useState<string[]>(pref.target_functions);
  const [locations, setLocations] = useState<string[]>(pref.target_locations);
  const [seniority, setSeniority] = useState<string>(pref.target_seniority ?? "");
  const [phase, setPhase] = useState<Phase>(
    needsFactsBuild(pref) ? { kind: "reading" } : { kind: "edit" },
  );
  const [error, setError] = useState<string | null>(null);
  // Whether a seed actually resulted, which is what the lead may claim. It
  // tracks the SERVER's answers, not the edits: a person clearing a chip has
  // not made the resume unread. False covers both a failed build and a resume
  // too thin to yield anything.
  const [seeded, setSeeded] = useState(
    pref.target_functions.length > 0 ||
      pref.target_locations.length > 0 ||
      (pref.target_seniority ?? "") !== "",
  );

  // The run outlives a click, not the card: leaving the page ends the loop.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // One build per mount, not one per render and not one per effect pass: it
  // is a metered provider call.
  const asked = useRef(false);
  useEffect(() => {
    if (!needsFactsBuild(pref) || asked.current) return;
    asked.current = true;
    void (async () => {
      try {
        const built = await refreshResumeFacts();
        // The endpoint reports an extraction fault in the body rather than as
        // a status, so a 200 is not yet a success.
        if (built.error !== null) throw new Error(built.error);
        const fresh = await fetchJobPreferences();
        if (!alive.current) return;
        setFunctions(fresh.target_functions);
        setLocations(fresh.target_locations);
        setSeniority(fresh.target_seniority ?? "");
        setSeeded(
          fresh.target_functions.length > 0 ||
            fresh.target_locations.length > 0 ||
            (fresh.target_seniority ?? "") !== "",
        );
      } catch {
        // A 429, an extraction fault, a dropped connection: the three
        // questions are still answerable by hand, and the lead below says
        // nothing was filled in rather than claiming the resume was read.
        if (alive.current) setSeeded(false);
      } finally {
        if (alive.current) {
          setPhase((p) => (p.kind === "reading" ? { kind: "edit" } : p));
        }
      }
    })();
    // The seed is handed in once per mount; a re-render with the same prop
    // must not ask again.
  }, [pref]);

  function label(keys: Record<string, JobsMessageKey>, value: string): string {
    const key = keys[value] ?? keys[value.toUpperCase()];
    return key ? tJobs(key) : value;
  }

  // Anything already held but missing from the closed list stays visible and
  // removable instead of surviving a save the person cannot see.
  const functionOptions = Object.keys(FUNCTION_KEYS).filter(
    (v) => !functions.includes(v),
  );
  const countryOptions = Object.keys(COUNTRY_KEYS)
    .map((c) => c.toLowerCase())
    .filter((c) => !locations.some((l) => l.toLowerCase() === c));
  // "unknown" is a filter on /jobs, not a thing to want; the API refuses it.
  const seniorityOptions = Object.keys(SENIORITY_KEYS).filter((v) => v !== "unknown");
  if (seniority && !seniorityOptions.includes(seniority)) seniorityOptions.push(seniority);

  const [refreshing, setRefreshing] = useState(false);

  // Every way out ends here. A refetch that fails must not leave a spinner
  // (or a Continue that does nothing) on the first screen a person sees after
  // typing their first answer: it lands in `stopped`, whose button retries.
  async function finish() {
    setRefreshing(true);
    try {
      await onDone();
    } catch {
      if (alive.current) setPhase({ kind: "stopped", notice: "onboardRefreshFailed" });
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }

  async function submit() {
    setError(null);
    setPhase({ kind: "saving" });
    let firstSave = false;
    try {
      const saved = await saveJobPreferences({
        target_functions: functions,
        target_locations: locations,
        target_seniority: seniority || null,
        // Not asked here, so not changed: the seed's value goes back as it came.
        remote_required: pref.remote_required,
        activity: pref.activity,
      });
      firstSave = saved.first_save;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("onboardSaveFailed"));
      setPhase({ kind: "edit" });
      return;
    }
    // Saved before anyone else: nothing to start. Reload and be done.
    if (!firstSave) {
      await finish();
      return;
    }
    setPhase({ kind: "scoring" });
    try {
      const run = await startFirstRun();
      if (!run.started) {
        const notice = firstRunNotice(run.reason);
        if (notice === null) await finish();
        else if (alive.current) setPhase({ kind: "stopped", notice });
        return;
      }
      for (let polls = 1; alive.current; polls += 1) {
        await sleep(POLL_MS);
        if (!alive.current) return;
        // A failed read is one wasted poll, not the end: the count is bounded
        // either way, and a flaky network should not look like a dead run.
        const progress = await fetchMatchProgress().catch(() => ({ scored: 0, expected: 1 }));
        const verdict = pollVerdict({ ...progress, polls });
        if (verdict === "done") {
          await finish();
          return;
        }
        if (verdict === "stop") {
          setPhase({ kind: "stopped", notice: "onboardSlow" });
          return;
        }
      }
    } catch {
      if (alive.current) setPhase({ kind: "stopped", notice: "onboardGeneric" });
    }
  }

  const busy = phase.kind !== "edit";

  function chips(values: string[], keys: Record<string, JobsMessageKey>, set: (v: string[]) => void) {
    return values.map((v) => (
      <button
        key={v}
        type="button"
        disabled={busy}
        onClick={() => set(removeValue(values, v))}
        aria-label={t("onboardRemove", { label: label(keys, v) })}
        className="inline-flex items-center gap-1 rounded-full border border-rule bg-sheet px-2.5 py-1 text-sm disabled:opacity-60"
      >
        <span>{label(keys, v)}</span>
        <X className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
      </button>
    ));
  }

  const selectClass =
    "min-w-0 max-w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm disabled:opacity-60";

  return (
    <section
      className="rr-card px-5 py-4"
      data-testid="dashboard-onboarding"
      aria-labelledby="dash-onboarding"
    >
      <h2
        id="dash-onboarding"
        className="font-display text-[1.15rem] font-medium tracking-[-0.025em]"
      >
        {t("onboardTitle")}
      </h2>
      {/* Conditional, because the unconditional sentence was false for the
          default new account: nothing had built the facts, so there was no
          seed to have been filled in from. And silent while the resume is
          being read, because neither answer is known yet -- "could not fill
          these in" is as wrong then as "we read your resume" was. */}
      {phase.kind === "reading" ? null : (
        <p className="mt-1 text-sm text-muted-foreground">
          {seeded ? t("onboardLead") : t("onboardLeadBlank")}
        </p>
      )}

      {phase.kind === "reading" || phase.kind === "scoring" ? (
        <p className="mt-4 flex items-start gap-2.5 text-sm" role="status">
          <Loader2
            className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-clay motion-reduce:animate-none"
            aria-hidden="true"
          />
          <span>
            {phase.kind === "reading" ? t("onboardReading") : t("onboardScoring")}
          </span>
        </p>
      ) : phase.kind === "stopped" ? (
        <div className="mt-4">
          <p className="text-sm" role="status">
            {t(phase.notice)}
          </p>
          <button
            type="button"
            className="rr-btn mt-3 whitespace-nowrap"
            disabled={refreshing}
            onClick={() => void finish()}
          >
            {t("onboardContinue")}
          </button>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-4">
            <div className="min-w-0">
              <p className="text-[0.8125rem] font-bold">{t("onboardQFunction")}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {chips(functions, FUNCTION_KEYS, setFunctions)}
                <select
                  className={selectClass}
                  value=""
                  disabled={busy}
                  aria-label={t("onboardQFunction")}
                  onChange={(e) => e.target.value && setFunctions(addValue(functions, e.target.value))}
                >
                  <option value="">
                    {functions.length ? t("onboardAddAnother") : t("onboardPick")}
                  </option>
                  {functionOptions.map((v) => (
                    <option key={v} value={v}>
                      {label(FUNCTION_KEYS, v)}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="min-w-0">
              <p className="text-[0.8125rem] font-bold">{t("onboardQLocation")}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {chips(locations, COUNTRY_KEYS, setLocations)}
                <select
                  className={selectClass}
                  value=""
                  disabled={busy}
                  aria-label={t("onboardQLocation")}
                  onChange={(e) => e.target.value && setLocations(addValue(locations, e.target.value))}
                >
                  <option value="">
                    {locations.length ? t("onboardAddAnother") : t("onboardAnywhere")}
                  </option>
                  {countryOptions.map((v) => (
                    <option key={v} value={v}>
                      {label(COUNTRY_KEYS, v)}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="min-w-0">
              <p className="text-[0.8125rem] font-bold">{t("onboardQSeniority")}</p>
              <div className="mt-1.5">
                <select
                  className={selectClass}
                  value={seniority}
                  disabled={busy}
                  aria-label={t("onboardQSeniority")}
                  onChange={(e) => setSeniority(e.target.value)}
                >
                  <option value="">{tJobs("seniorityAll")}</option>
                  {seniorityOptions.map((v) => (
                    <option key={v} value={v}>
                      {label(SENIORITY_KEYS, v)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* A thin or unparseable resume seeds nothing; a disabled button with
              no reason is a dead end for exactly the person this card is for. */}
          {!canSave({ functions, locations, seniority }) ? (
            <p className="mt-3 text-sm text-muted-foreground">{t("onboardEmptyHint")}</p>
          ) : null}

          {error ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
            <button
              type="button"
              className="rr-btn whitespace-nowrap"
              disabled={busy || !canSave({ functions, locations, seniority })}
              onClick={() => void submit()}
            >
              {phase.kind === "saving" ? t("onboardSaving") : t("onboardSave")}
            </button>
            <Link href="/preferences" className="text-sm underline underline-offset-2">
              {t("onboardMoreOptions")}
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
