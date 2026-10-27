"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import UsageMeter from "@/components/UsageMeter";
import { DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { fetchUsage, type Usage } from "@/lib/api";

/**
 * This week's AI allowance, inside the account menu.
 *
 * The editor already shows this meter beside the chat that spends it, and
 * /settings shows the full card. What was missing was a glance from anywhere
 * else, so the account menu -- the one control on every page -- gets the same
 * meter rather than a second, differently worded summary of the same numbers.
 *
 * Radix mounts menu content only while the menu is open, so this fetches once
 * per open, which is the behaviour we want: the balance moves while you edit,
 * and a figure cached at page load would be stale by the time anyone looked.
 *
 * Renders nothing until it has an answer, and sits directly under the identity
 * row at the top of the menu: the two together are "which account this is and
 * what it has left". The cost of that placement is that a slow reply pushes the
 * actions below down once -- the menu is capped at the meter's own 14rem width,
 * so it grows downwards only, and the fetch is one request against a menu that
 * stays usable the whole time.
 *
 * It no longer carries its own follow-through link. "Plan & usage" is a static
 * item in Nav, above Settings, so it is there whether or not this fetch
 * succeeds and whether or not limits are enforced -- a link that disappeared
 * with the meter took the only route to the plan page with it.
 */
export default function NavUsage() {
  const t = useTranslations("nav");
  const [usage, setUsage] = useState<Usage | null>(null);

  useEffect(() => {
    let live = true;
    fetchUsage()
      .then((next) => {
        if (live) setUsage(next);
      })
      .catch(() => {
        // No meter is better than an error in a menu that has to stay usable.
      });
    return () => {
      live = false;
    };
  }, []);

  if (!usage || !usage.enforced) return null;

  // Exactly one side of the tier is capped: a guest on a turn count, an
  // account on cost. Read whichever is set.
  const countCapped = usage.turn_limit !== null;
  const used = countCapped ? usage.turns_used : usage.used_credits;
  const limit = countCapped ? usage.turn_limit : usage.limit_credits;
  if (limit === null || limit <= 0) return null;

  // A turn-capped tier's own "3 of 4" already is the edit count, so the
  // estimate would just repeat it. At zero it is dropped too: "about 0 more
  // edits" is a worse way to say what the full bar and "20 of 20" already say,
  // and unlike the editor there is no limit card here to carry the meaning.
  const editsLeft = usage.approx_edits_left;
  const editsLeftLabel =
    !countCapped && typeof editsLeft === "number" && editsLeft > 0
      ? t("usageEditsLeft", { count: editsLeft })
      : null;

  // The separator trails the meter rather than leading it: the identity row
  // above already closes with one, and a leading separator here would double it
  // up the moment the fetch landed.
  return (
    <>
      <div className="nav-usage px-2 py-1.5">
        <UsageMeter
          usage={usage}
          label={countCapped ? t("usageEdits", { used, limit }) : t("usageCredits", { used, limit })}
          editsLeftLabel={editsLeftLabel}
          resetLabel={t("usageResets")}
        />
      </div>
      <DropdownMenuSeparator />
    </>
  );
}
