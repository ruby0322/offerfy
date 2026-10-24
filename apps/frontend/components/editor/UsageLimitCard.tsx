"use client";

import { Link } from "@/i18n/navigation";
import type { UsageRefusal } from "@/lib/api";

type Props = {
  refusal: UsageRefusal;
  title: string;
  /** What the next tier would give, already formatted. Null at the top tier. */
  offer: string | null;
  resetLabel: string;
  /** Shown for a guest, or when the guest pool ceiling fired. */
  signInLabel: string | null;
  settingsLabel: string | null;
  learnMoreLabel: string;
  onDismiss: () => void;
  dismissLabel: string;
};

/**
 * What to do now that this week's AI editing has run out.
 *
 * A card rather than a toast, because at this moment it is the most important
 * thing on screen — and because for a guest this *is* the sign-up moment and
 * for a signed-in person the matching moment. A toast that fades wastes both.
 *
 * Worded as an offer, never as a wall: nothing here is lost, the resume is
 * still editable by hand, and the PDF still exports.
 */
export default function UsageLimitCard({
  refusal,
  title,
  offer,
  resetLabel,
  signInLabel,
  settingsLabel,
  learnMoreLabel,
  onDismiss,
  dismissLabel,
}: Props) {
  const showSignIn = Boolean(signInLabel) && (refusal.tier === "guest" || refusal.sign_in_required);
  const showSettings = Boolean(settingsLabel) && !showSignIn && refusal.next_tier?.name === "matching";

  return (
    <div className="usage-limit-card" role="status">
      <p className="usage-limit-title">{title}</p>
      <p className="usage-limit-detail">{refusal.detail}</p>
      {offer ? <p className="usage-limit-offer">{offer}</p> : null}
      <p className="usage-limit-reset">{resetLabel}</p>
      <div className="usage-limit-actions">
        {showSignIn ? (
          <Link href="/login" className="usage-limit-primary">
            {signInLabel}
          </Link>
        ) : null}
        {showSettings ? (
          <Link href="/settings" className="usage-limit-primary">
            {settingsLabel}
          </Link>
        ) : null}
        <Link href="/usage" className="usage-limit-secondary">
          {learnMoreLabel}
        </Link>
        <button type="button" className="usage-limit-dismiss" onClick={onDismiss}>
          {dismissLabel}
        </button>
      </div>
    </div>
  );
}
