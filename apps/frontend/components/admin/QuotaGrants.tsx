"use client";

import ConfirmDeleteDialog from "@/components/ConfirmDeleteDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  adminIssueGrant,
  adminRevokeGrant,
  type AdminQuotaGrant,
  type AdminUserUsage,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import { useState } from "react";

const REASONS = ["manual", "compensation", "beta", "staff", "promotion"] as const;

type Reason = (typeof REASONS)[number];

function reasonKey(reason: string) {
  switch (reason) {
    case "compensation":
      return "grantsReason_compensation" as const;
    case "beta":
      return "grantsReason_beta" as const;
    case "staff":
      return "grantsReason_staff" as const;
    case "promotion":
      return "grantsReason_promotion" as const;
    default:
      return "grantsReason_manual" as const;
  }
}

/** The server decides; this only names it. See UsageGrant.status. */
function statusKey(grant: AdminQuotaGrant) {
  switch (grant.status) {
    case "revoked":
      return "grantsStatusRevoked" as const;
    case "scheduled":
      return "grantsStatusPending" as const;
    case "expired":
      return "grantsStatusLapsed" as const;
    default:
      return "grantsStatusActive" as const;
  }
}

function day(value: string) {
  return value.slice(0, 10);
}

type Props = {
  userId: string;
  grants: AdminQuotaGrant[];
  usage: AdminUserUsage;
  /** Refetch the whole detail: a grant moves the ceiling, so the numbers
      above this table go stale the moment one lands. */
  onChanged: () => void;
};

export default function QuotaGrants({ userId, grants, usage, onChanged }: Props) {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");

  const [credits, setCredits] = useState("");
  const [reason, setReason] = useState<Reason>("manual");
  const [detail, setDetail] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [revoking, setRevoking] = useState<AdminQuotaGrant | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const amount = Number(credits);
  const canSubmit = !busy && Number.isInteger(amount) && amount > 0;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await adminIssueGrant(userId, {
        credits: amount,
        reason,
        detail: detail.trim() || null,
        expires_at: expiresAt || null,
      });
      setCredits("");
      setDetail("");
      setExpiresAt("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("grantsFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!revoking) return;
    setRevokeBusy(true);
    setRevokeError(null);
    try {
      await adminRevokeGrant(userId, revoking.id);
      setRevoking(null);
      onChanged();
    } catch (err) {
      setRevokeError(err instanceof Error ? err.message : t("grantsFailed"));
    } finally {
      setRevokeBusy(false);
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{t("grantsTitle")}</h2>
      {/* The window rule is the surprising part, so it is written down where
          someone is about to issue one rather than only in the model. */}
      <p className="text-xs text-muted-foreground">{t("grantsNote")}</p>

      {usage.tier_credits !== null && usage.granted_credits > 0 ? (
        <p className="text-sm tabular-nums">
          {t("grantsCeiling", {
            tier: usage.tier_credits,
            granted: usage.granted_credits,
            total: usage.limit_credits ?? usage.tier_credits + usage.granted_credits,
          })}
        </p>
      ) : null}

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted-foreground">
            {t("grantsCredits")}
          </span>
          <Input
            type="number"
            min={1}
            step={1}
            required
            value={credits}
            onChange={(e) => setCredits(e.target.value)}
            className="w-24"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted-foreground">
            {t("grantsReason")}
          </span>
          <select
            value={reason}
            onChange={(e) => setReason(e.target.value as Reason)}
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            {REASONS.map((value) => (
              <option key={value} value={value}>
                {t(reasonKey(value))}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-48 flex-1 flex-col gap-1">
          <span className="text-xs uppercase text-muted-foreground">
            {t("grantsDetail")}
          </span>
          <Input
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder={t("grantsDetailHint")}
            maxLength={255}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted-foreground">
            {t("grantsExpires")}
          </span>
          <Input
            type="date"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            title={t("grantsExpiresHint")}
            className="w-40"
          />
        </label>
        <Button type="submit" disabled={!canSubmit}>
          {busy ? t("grantsSaving") : t("grantsSubmit")}
        </Button>
      </form>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {grants.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("grantsNone")}</p>
      ) : (
        <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-2 pr-4 font-medium">{t("grantsColCredits")}</th>
              <th className="py-2 pr-4 font-medium">{t("grantsColReason")}</th>
              <th className="py-2 pr-4 font-medium">{t("grantsColWindow")}</th>
              <th className="py-2 pr-4 font-medium">{t("grantsColIssuedBy")}</th>
              <th className="py-2 pr-4 font-medium">{t("grantsColStatus")}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {grants.map((row) => (
              <tr key={row.id} className="border-b border-border/70">
                <td className="py-2 pr-4 tabular-nums">+{row.credits}</td>
                <td className="py-2 pr-4">
                  {t(reasonKey(row.reason))}
                  {row.detail ? (
                    <span className="text-muted-foreground"> · {row.detail}</span>
                  ) : null}
                </td>
                <td className="py-2 pr-4 whitespace-nowrap tabular-nums">
                  {day(row.starts_at)}
                  {" → "}
                  {row.expires_at ? (
                    day(row.expires_at)
                  ) : (
                    <span className="text-muted-foreground">
                      {t("grantsOpenEnded")}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 text-muted-foreground">
                  {row.issued_by ?? "—"}
                </td>
                <td className="py-2 pr-4">
                  <span className={row.active ? "font-medium" : "text-muted-foreground"}>
                    {t(statusKey(row))}
                  </span>
                </td>
                <td className="py-2 text-right">
                  {/* Only where it would do something. A lapsed grant cannot
                      come back -- expires_at is in the past and a grant is
                      never edited -- so Revoke there is dead UI. */}
                  {row.status === "active" || row.status === "scheduled" ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setRevokeError(null);
                        setRevoking(row);
                      }}
                    >
                      {t("grantsRevoke")}
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {revoking ? (
        <ConfirmDeleteDialog
          title={t("grantsRevokeTitle")}
          description={t("grantsRevokeBody")}
          busy={revokeBusy}
          error={revokeError}
          onConfirm={revoke}
          onClose={() => setRevoking(null)}
          labels={{
            confirm: t("grantsRevokeConfirm"),
            confirming: t("grantsRevoking"),
            cancel: tCommon("deleteCancel"),
          }}
        />
      ) : null}
    </section>
  );
}
