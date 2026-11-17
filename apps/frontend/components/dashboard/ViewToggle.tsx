"use client";

import { useTranslations } from "next-intl";

/**
 * List or cards, for a section that can be read both ways.
 *
 * The same control the resumes tab uses, in the same markup, because it is
 * the same question asked about a different list -- and two switches that
 * look alike but behave differently are worse than one that looks dull.
 *
 * It is stateless on purpose. The Matches tab draws it twice, once over each
 * section, and both copies read and write ONE preference: the choice is about
 * how the page is read, not about which half of it you are looking at.
 */

export const DASHBOARD_VIEWS = ["list", "cards"] as const;

export type DashboardView = (typeof DASHBOARD_VIEWS)[number];

type Props = {
  view: DashboardView;
  choose: (next: DashboardView) => void;
};

export default function ViewToggle({ view, choose }: Props) {
  const t = useTranslations("dashboard");
  return (
    // rr-switch owns the look, down to the touch target: see globals.css. The
    // chosen option is read off aria-pressed, so there is no second way to
    // say which one is on.
    <span className="rr-switch shrink-0">
      {DASHBOARD_VIEWS.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={view === option}
          onClick={() => choose(option)}
        >
          {t(option === "list" ? "viewList" : "viewCards")}
        </button>
      ))}
    </span>
  );
}
