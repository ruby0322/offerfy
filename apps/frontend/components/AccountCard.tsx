"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  accountFieldErrorOf,
  getAccount,
  patchAccount,
  type Account,
  type AccountField,
} from "@/lib/api";

/**
 * Who you are, on the settings page.
 *
 * One Save for the whole form rather than a field-at-a-time autosave. The
 * fields are related -- a handle and a name and a line about yourself are one
 * act of filling something in -- and an autosaving handle field would have to
 * decide what a half-typed handle means while you are still typing it.
 *
 * The form holds strings, never nulls, because an input's value cannot be
 * null. What goes back on save is the inverse: an emptied field is sent as
 * `null`, which is how the API spells "clear this one". A field that was
 * already empty and is still empty is not sent at all, so saving a name never
 * writes a null over a column the server might have normalised differently.
 */

const FIELDS = ["username", "display_name", "headline", "location", "website", "bio"] as const;

type Form = Record<AccountField, string>;

/**
 * The server's `field` + `code` pairs, mapped to the sentence for each.
 *
 * Spelled out rather than built as `` `error_${field}_${code}` ``, because
 * next-intl types its message keys and a template literal is not one of them
 * — the same reason PricingPage has its `nameKey` helper. The upside is that
 * a code the backend adds without copy here falls through to the generic
 * message instead of rendering a raw key at someone.
 */
type RefusalKey =
  | "error_username_length"
  | "error_username_charset"
  | "error_username_reserved"
  | "error_username_taken"
  | "error_website_invalid"
  | "error_too_long";

const REFUSALS: Record<string, RefusalKey | undefined> = {
  "username:length": "error_username_length",
  "username:charset": "error_username_charset",
  "username:reserved": "error_username_reserved",
  "username:taken": "error_username_taken",
  "display_name:length": "error_too_long",
  "headline:length": "error_too_long",
  "location:length": "error_too_long",
  "bio:length": "error_too_long",
  "website:length": "error_too_long",
  "website:scheme": "error_website_invalid",
  "website:host": "error_website_invalid",
};

function toForm(account: Account): Form {
  return {
    username: account.username ?? "",
    display_name: account.display_name ?? "",
    headline: account.headline ?? "",
    location: account.location ?? "",
    website: account.website ?? "",
    bio: account.bio ?? "",
  };
}

/** Only what changed, with "" sent as null so the server clears the column. */
function changes(form: Form, saved: Form): Partial<Record<AccountField, string | null>> {
  const diff: Partial<Record<AccountField, string | null>> = {};
  for (const field of FIELDS) {
    if (form[field] === saved[field]) continue;
    diff[field] = form[field].trim() === "" ? null : form[field];
  }
  return diff;
}

export default function AccountCard() {
  const t = useTranslations("account");
  const tCommon = useTranslations("common");
  const [account, setAccount] = useState<Account | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [saved, setSaved] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<{ field: AccountField; message: string } | null>(
    null,
  );
  const [done, setDone] = useState(false);

  useEffect(() => {
    let live = true;
    getAccount()
      .then((next) => {
        if (!live) return;
        setAccount(next);
        setForm(toForm(next));
        setSaved(toForm(next));
      })
      .catch(() => {
        if (live) setError(t("loadFailed"));
      });
    return () => {
      live = false;
    };
  }, [t]);

  function set(field: AccountField, value: string) {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev));
    setDone(false);
    // Clear the mark on the field being retyped, but leave a mark on any
    // other field standing: it is still true.
    setFieldError((prev) => (prev?.field === field ? null : prev));
  }

  async function onSave() {
    if (!form || !saved || busy) return;
    const diff = changes(form, saved);
    if (Object.keys(diff).length === 0) {
      setDone(true);
      return;
    }
    setBusy(true);
    setError(null);
    setFieldError(null);
    try {
      const next = await patchAccount(diff);
      setAccount(next);
      // From the response, not from the form: the server normalises, and the
      // person should see `https://ruby.im` where they typed `ruby.im` rather
      // than discover it on the next page load.
      setForm(toForm(next));
      setSaved(toForm(next));
      setDone(true);
    } catch (err) {
      const refused = accountFieldErrorOf(err);
      const key = refused ? REFUSALS[`${refused.field}:${refused.code}`] : undefined;
      if (refused && key) {
        setFieldError({ field: refused.field, message: t(key) });
      } else {
        // Either not a field refusal, or a code this build has no sentence
        // for. Both read better as "that did not save" than as a raw key.
        setError(t("saveFailed"));
      }
    } finally {
      setBusy(false);
    }
  }

  if (error && !form) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">{error}</p>
        </CardContent>
      </Card>
    );
  }

  if (!form || !account) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{tCommon("loading")}</p>
        </CardContent>
      </Card>
    );
  }

  const mark = (field: AccountField) =>
    fieldError?.field === field ? { "aria-invalid": true as const } : {};
  const note = (field: AccountField, hint: string) => (
    <span
      className={
        fieldError?.field === field ? "text-xs text-destructive" : "text-xs text-muted-foreground"
      }
    >
      {fieldError?.field === field ? fieldError.message : hint}
    </span>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("notice")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {/* The avatar and the email together, because both are changed by
            signing in again rather than here, and a form that shows half of
            someone's identity and hides the other half invites hunting for
            the missing control. */}
        <div className="flex items-center gap-3">
          <Avatar className="size-10">
            {account.picture ? (
              <AvatarImage src={account.picture} alt="" referrerPolicy="no-referrer" />
            ) : null}
            <AvatarFallback className="text-xs font-medium">
              {(account.email || "?")[0]!.toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{account.email}</p>
            <p className="text-xs text-muted-foreground">{t("emailFixed")}</p>
          </div>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("usernameLabel")}</span>
          {/* text-base on a phone, md:text-sm above it. Anything smaller and
              iOS zooms the page on focus and never zooms back -- AGENTS.md §7. */}
          <Input
            className="text-base md:text-sm"
            value={form.username}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            maxLength={30}
            placeholder={t("usernamePlaceholder")}
            disabled={busy}
            onChange={(event) => set("username", event.target.value)}
            {...mark("username")}
          />
          {note("username", t("usernameHint"))}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("displayNameLabel")}</span>
          <Input
            className="text-base md:text-sm"
            value={form.display_name}
            maxLength={80}
            // Google's name as the placeholder, so leaving this empty visibly
            // means "use that" rather than "have no name".
            placeholder={account.google_name ?? t("displayNamePlaceholder")}
            disabled={busy}
            onChange={(event) => set("display_name", event.target.value)}
            {...mark("display_name")}
          />
          {note("display_name", t("displayNameHint"))}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("headlineLabel")}</span>
          <Input
            className="text-base md:text-sm"
            value={form.headline}
            maxLength={120}
            placeholder={t("headlinePlaceholder")}
            disabled={busy}
            onChange={(event) => set("headline", event.target.value)}
            {...mark("headline")}
          />
          {note("headline", t("headlineHint"))}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("locationLabel")}</span>
          <Input
            className="text-base md:text-sm"
            value={form.location}
            maxLength={80}
            placeholder={t("locationPlaceholder")}
            disabled={busy}
            onChange={(event) => set("location", event.target.value)}
            {...mark("location")}
          />
          {note("location", t("locationHint"))}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("websiteLabel")}</span>
          <Input
            className="text-base md:text-sm"
            type="url"
            inputMode="url"
            value={form.website}
            autoCapitalize="none"
            spellCheck={false}
            maxLength={255}
            placeholder={t("websitePlaceholder")}
            disabled={busy}
            onChange={(event) => set("website", event.target.value)}
            {...mark("website")}
          />
          {note("website", t("websiteHint"))}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t("bioLabel")}</span>
          <Textarea
            className="min-h-24 text-base md:text-sm"
            value={form.bio}
            maxLength={600}
            placeholder={t("bioPlaceholder")}
            disabled={busy}
            onChange={(event) => set("bio", event.target.value)}
            {...mark("bio")}
          />
          {note("bio", t("bioHint", { max: 600 }))}
        </label>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" size="sm" disabled={busy} onClick={() => void onSave()}>
            {busy ? t("saving") : t("save")}
          </Button>
          {done && !busy ? <p className="text-xs text-muted-foreground">{t("saved")}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}
