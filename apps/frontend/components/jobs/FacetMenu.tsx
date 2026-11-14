"use client";

import { useState, type ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { useHoverMenu } from "@/lib/use-hover-menu";

export type FacetOption = {
  value: string;
  label: string;
  count?: number;
};

type Props = {
  /** What the button says when nothing is chosen. */
  label: string;
  /** The chosen option's label, which replaces the button text when set. */
  selected?: string;
  /** Href that clears this facet, used by the × and by "Any". */
  clearHref: string;
  /** Accessible name for the ×, e.g. "Clear function". */
  clearLabel: string;
  hrefFor: (value: string) => string;
  options: FacetOption[];
  value?: string;
  anyLabel: string;
  /** Two columns, for a long vocabulary like function. */
  wide?: boolean;
  /** A filter box above the options, for a vocabulary too long to scan. */
  findLabel?: string;
  /** Rendered under the options, e.g. "All 88 employers". */
  footer?: ReactNode;
  heading: string;
  /** Closes the phone sheet; unused above the phone breakpoint. */
  doneLabel: string;
};

/**
 * One facet, with its options inside it.
 *
 * This is the whole point of the filter bar's shape: a facet costs one
 * button whether it has two options or eighty-eight, so the row stays one
 * row as facets are added. The previous layout put every option on the page
 * as a pill, which grew with facets × options and had already reached three
 * stacked rows at six facets.
 *
 * Options are links, not buttons, so a filter is a navigation with a URL —
 * shareable, back-button-able, and working with JavaScript off. The menu
 * itself needs JavaScript; the links inside it do not.
 */
export default function FacetMenu({
  label,
  selected,
  clearHref,
  clearLabel,
  hrefFor,
  options,
  value,
  anyLabel,
  wide,
  findLabel,
  footer,
  heading,
  doneLabel,
}: Props) {
  const { open, box, hover, toggle, close } = useHoverMenu<HTMLSpanElement>();
  const [find, setFind] = useState("");

  const needle = find.trim().toLowerCase();
  const shown = needle
    ? options.filter((option) => option.label.toLowerCase().includes(needle))
    : options;

  return (
    <span
      className="jobs-facet"
      data-open={open ? "1" : "0"}
      ref={box}
      {...hover}
    >
      {/* Set and unset are different controls, not one button with two
          meanings: when a facet is set the × has to clear it in one click,
          and a link inside a button is not valid markup. They are styled as
          one pill. */}
      <span className={`jobs-facet-pill${selected ? " is-set" : ""}`}>
        <button
          type="button"
          className="jobs-facet-btn"
          aria-expanded={open}
          aria-haspopup="menu"
          onClick={toggle}
        >
          {selected ?? label}
          {selected ? null : (
            <span className="jobs-facet-caret" aria-hidden="true">
              ▾
            </span>
          )}
        </button>
        {selected ? (
          <Link href={clearHref} className="jobs-facet-clear" aria-label={clearLabel}>
            <span aria-hidden="true">×</span>
          </Link>
        ) : null}
      </span>
      {/* On a phone the panel is a sheet over the page, so it needs the two
          things a sheet needs: something dimmed behind it, and a way out
          that is not "tap the exact right nothing". Both are inert above
          the phone breakpoint, where the panel is a popover under its
          pill. The scrim is a child of the facet, so the hook's
          outside-click listener will not see a tap on it -- it closes the
          sheet itself. */}
      <span className="jobs-sheet-scrim" aria-hidden="true" onMouseDown={close} />
      <div className={`jobs-menu${wide ? " is-wide" : ""}`} role="menu">
        <button type="button" className="jobs-sheet-done" onClick={close}>
          {doneLabel}
        </button>
        <h3>{heading}</h3>
        {findLabel ? (
          <input
            type="search"
            className="jobs-menu-find"
            placeholder={findLabel}
            aria-label={findLabel}
            value={find}
            onChange={(event) => setFind(event.target.value)}
          />
        ) : null}
        <Link
          href={clearHref}
          className={`jobs-opt${value ? "" : " is-on"}`}
          role="menuitem"
          onClick={close}
        >
          {anyLabel}
        </Link>
        <div className={wide ? "jobs-menu-two" : undefined}>
          {shown.map((option) => (
            <Link
              key={option.value}
              href={hrefFor(option.value)}
              className={`jobs-opt${option.value === value ? " is-on" : ""}`}
              role="menuitem"
              onClick={close}
            >
              <span>{option.label}</span>
              {typeof option.count === "number" ? (
                <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
              ) : null}
            </Link>
          ))}
        </div>
        {footer ? <div className="jobs-menu-foot">{footer}</div> : null}
      </div>
    </span>
  );
}
