"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "@/i18n/navigation";

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
}: Props) {
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState("");
  const box = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const needle = find.trim().toLowerCase();
  const shown = needle
    ? options.filter((option) => option.label.toLowerCase().includes(needle))
    : options;

  return (
    <span
      className="jobs-facet"
      data-open={open ? "1" : "0"}
      ref={box}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
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
          onClick={() => setOpen((current) => !current)}
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
      <div className={`jobs-menu${wide ? " is-wide" : ""}`} role="menu">
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
          onClick={() => setOpen(false)}
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
              onClick={() => setOpen(false)}
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
