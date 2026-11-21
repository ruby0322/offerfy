"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  agentKeyErrorCode,
  agentKeyIsLive,
  createAgentKey,
  listAgentKeyCalls,
  listAgentKeys,
  revokeAgentKey,
  type AgentKey,
  type AgentKeyCreated,
  type AgentToolCall,
} from "@/lib/api";

/**
 * Where a person mints, sees and revokes the credentials their own AI
 * assistant connects with.
 *
 * The raw token lives in `fresh`, component state and nowhere else -- not
 * storage, not the URL, not the key list. The server returns it from one call
 * and cannot produce it again, so a reload (or pressing "I have saved it")
 * loses it, which is the behaviour the warning promises.
 *
 * `matchingOn` comes from the page, which already read the consent for its own
 * toggle; this card makes no request of its own to find out.
 */

const OUTCOMES = {
  ok: "outcome_ok",
  refused: "outcome_refused",
  error: "outcome_error",
  throttled: "outcome_throttled",
} as const;

type OutcomeKey = (typeof OUTCOMES)[keyof typeof OUTCOMES];

function outcomeKey(outcome: string): OutcomeKey | null {
  return outcome in OUTCOMES ? OUTCOMES[outcome as keyof typeof OUTCOMES] : null;
}

/**
 * The server's own "no such key of yours" answer, as opposed to any other 404.
 * `_own_key` in app/routers/account.py sends this detail for a key that is
 * missing, already revoked, or someone else's -- all of which mean the key the
 * person asked to revoke is not live. A 404 from a misrouted path or a proxy
 * carries something else, and must not be read as success.
 */
const KEY_NOT_FOUND = "Key not found";

function isAlreadyGone(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 404) return false;
  const body = err.body as { detail?: unknown } | null;
  return body?.detail === KEY_NOT_FOUND;
}

export default function AgentKeys({ matchingOn }: { matchingOn: boolean }) {
  const t = useTranslations("agentKeys");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const [keys, setKeys] = useState<AgentKey[] | null>(null);
  const [label, setLabel] = useState("");
  const [fresh, setFresh] = useState<AgentKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set when the list could not be re-read after a change that DID succeed.
  // Kept apart from `error` on purpose: this is "the screen may be behind",
  // never "the thing you did failed".
  const [stale, setStale] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [calls, setCalls] = useState<Record<string, AgentToolCall[] | "failed">>({});
  // "" on the server and during hydration, the real origin afterwards, so the
  // two renders agree without setting state inside an effect.
  const origin = useSyncExternalStore(
    () => () => {},
    () => window.location.origin,
    () => "",
  );

  const day = useCallback(
    (iso: string) => new Date(iso).toLocaleDateString(locale, { dateStyle: "medium" }),
    [locale],
  );
  const moment = useCallback(
    (iso: string) =>
      new Date(iso).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" }),
    [locale],
  );

  useEffect(() => {
    let cancelled = false;
    listAgentKeys()
      .then((rows) => {
        if (!cancelled) setKeys(rows);
      })
      .catch(() => {
        if (!cancelled) {
          setKeys([]);
          setError(t("loadFailed"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  /**
   * Re-read the list after a change. Never throws, and never reports a failure
   * of the change itself: the change already succeeded by the time this runs.
   */
  async function refreshKeys() {
    try {
      setKeys(await listAgentKeys());
      setStale(false);
    } catch {
      setStale(true);
    }
  }

  async function generate(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    let made: AgentKeyCreated;
    try {
      made = await createAgentKey(label.trim());
    } catch (err) {
      const code = agentKeyErrorCode(err);
      setError(
        code === "too_many_keys"
          ? t("errorTooMany")
          : code === "invalid_label"
            ? t("errorLabel")
            : t("errorGeneric"),
      );
      setBusy(false);
      return;
    }
    // The key exists now. Show the token and the key first, from what the mint
    // returned, so a failed re-list below cannot hide a live credential or
    // sit an error next to a secret the person is deciding whether to copy.
    const listed: AgentKey = {
      id: made.id,
      label: made.label,
      created_at: made.created_at,
      last_used_at: made.last_used_at,
      expires_at: made.expires_at,
      revoked_at: null,
    };
    setFresh(made);
    setCopied(false);
    setLabel("");
    setKeys((prev) => [...(prev ?? []), listed]);
    await refreshKeys();
    setBusy(false);
  }

  async function copy() {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.token);
      setCopied(true);
    } catch {
      // Clipboard can be blocked; the token is selectable on screen as well.
      setCopied(false);
    }
  }

  async function revoke(id: string) {
    setRevoking(id);
    setError(null);
    try {
      await revokeAgentKey(id);
    } catch (err) {
      // A 404 with the server's own body means the key is already gone
      // (revoked in another tab, say), which is the outcome that was asked
      // for. A 404 from a misrouted path or a proxy has the same status and a
      // different body, and treating that as success would leave a live key
      // hidden behind the muted stale note if the re-list below also failed.
      if (!isAlreadyGone(err)) {
        setError(t("errorGeneric"));
        setRevoking(null);
        return;
      }
    }
    // Revoked. Mark it locally first so it cannot linger as "live" if the
    // re-list below fails; the server keeps retired keys listed, with their log.
    if (fresh?.id === id) setFresh(null);
    setKeys((prev) =>
      (prev ?? []).map((key) =>
        key.id === id ? { ...key, revoked_at: new Date().toISOString() } : key,
      ),
    );
    await refreshKeys();
    setRevoking(null);
  }

  async function toggleCalls(id: string) {
    if (open === id) {
      setOpen(null);
      return;
    }
    setOpen(id);
    // Fetched on every open: the log is the thing that changes while an
    // assistant works, and a stale one would be a misleading disclosure.
    try {
      const rows = await listAgentKeyCalls(id);
      setCalls((prev) => ({ ...prev, [id]: rows }));
    } catch {
      setCalls((prev) => ({ ...prev, [id]: "failed" }));
    }
  }

  const endpoint = `${origin}/api/mcp`;

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("notice")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {matchingOn ? (
          <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-xs leading-5">
            {t("matchingWarning")}
          </p>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {stale ? (
          <p role="status" className="text-xs leading-5 text-muted-foreground">
            {t("listStale")}
          </p>
        ) : null}

        <form onSubmit={(event) => void generate(event)} className="flex flex-col gap-1.5">
          <label htmlFor="agent-key-label" className="text-sm font-medium">
            {t("labelField")}
          </label>
          <div className="flex flex-wrap gap-2">
            <Input
              id="agent-key-label"
              className="min-w-0 flex-1 basis-48"
              value={label}
              maxLength={255}
              placeholder={t("labelPlaceholder")}
              onChange={(event) => setLabel(event.target.value)}
            />
            <Button type="submit" size="sm" className="h-9" disabled={busy || !label.trim()}>
              {busy ? t("generating") : t("generate")}
            </Button>
          </div>
        </form>

        {fresh ? (
          <div
            role="alert"
            className="flex flex-col gap-2 rounded-md border border-border bg-muted/50 p-3"
          >
            <p className="text-sm font-medium">{t("newKeyTitle")}</p>
            <code
              data-testid="agent-key-token"
              className="select-all break-all rounded bg-background px-2 py-1.5 font-mono text-xs"
            >
              {fresh.token}
            </code>
            <p className="text-xs leading-5 text-destructive">{t("newKeyWarning")}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
                {copied ? t("copied") : t("copy")}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setFresh(null)}>
                {t("done")}
              </Button>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("yourKeys")}</h3>
          {keys == null ? (
            <p className="text-xs text-muted-foreground">{tCommon("loading")}</p>
          ) : keys.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("none")}</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
              {keys.map((key) => {
                const rows = calls[key.id];
                const live = agentKeyIsLive(key);
                return (
                  <li key={key.id} className="flex flex-col gap-2 p-3">
                    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                      <div className="min-w-0 flex-1 basis-48">
                        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 break-words text-sm font-medium">
                          {/* min-w-0 so a long label breaks inside its own
                              span instead of pushing the badge out of the
                              cell; CJK labels are ~1.5x wider than the English
                              ones this card was first laid out with. */}
                          <span className="min-w-0 break-words">{key.label}</span>
                          {live ? null : (
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
                              {key.revoked_at ? t("revoked") : t("expired")}
                            </span>
                          )}
                        </p>
                        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                          {t("created", { date: day(key.created_at) })}
                          {" · "}
                          {key.last_used_at
                            ? t("lastUsed", { date: moment(key.last_used_at) })
                            : t("neverUsed")}
                          {key.expires_at
                            ? ` · ${t("expires", { date: day(key.expires_at) })}`
                            : ""}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          aria-expanded={open === key.id}
                          onClick={() => void toggleCalls(key.id)}
                        >
                          {open === key.id ? t("hideRecent") : t("recent")}
                        </Button>
                        {live ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={revoking === key.id}
                            onClick={() => void revoke(key.id)}
                          >
                            {revoking === key.id ? t("revoking") : t("revoke")}
                          </Button>
                        ) : null}
                      </div>
                    </div>

                    {open === key.id ? (
                      <div className="flex flex-col gap-1.5 rounded-md bg-muted/40 p-2.5">
                        <p className="text-xs leading-5 text-muted-foreground">
                          {t("callsNote")}
                        </p>
                        {rows == null ? (
                          <p className="text-xs text-muted-foreground">{tCommon("loading")}</p>
                        ) : rows === "failed" ? (
                          <p className="text-xs text-destructive">{t("errorGeneric")}</p>
                        ) : rows.length === 0 ? (
                          <p className="text-xs text-muted-foreground">{t("noCalls")}</p>
                        ) : (
                          <ul className="flex flex-col gap-1">
                            {rows.map((call) => {
                              const known = outcomeKey(call.outcome);
                              return (
                                <li
                                  key={call.id}
                                  className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs"
                                >
                                  <span className="font-mono">{call.tool}</span>
                                  <span
                                    className={
                                      call.outcome === "ok"
                                        ? "text-muted-foreground"
                                        : "text-destructive"
                                    }
                                  >
                                    {known ? t(known) : call.outcome}
                                  </span>
                                  <span className="text-muted-foreground">
                                    {moment(call.created_at)}
                                  </span>
                                  {call.resume_id ? (
                                    <span className="text-muted-foreground">
                                      {t("resume", { id: call.resume_id.slice(0, 8) })}
                                    </span>
                                  ) : null}
                                </li>
                              );
                            })}
                          </ul>
                        )}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-border pt-4">
          <h3 className="text-sm font-medium">{t("howTitle")}</h3>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{t("howEndpoint")}</span>
            <code className="break-all rounded bg-muted px-2 py-1.5 font-mono text-xs">
              {endpoint}
            </code>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{t("howHeader")}</span>
            <code className="break-all rounded bg-muted px-2 py-1.5 font-mono text-xs">
              Authorization: Bearer ofa_…
            </code>
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            {t("howSpace")} {t("howPlaceholder")}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
