"use client";

import { useEffect, useRef, useState } from "react";
import { useLocale } from "next-intl";

export type TailorMaster = {
  id: string;
  title: string | null;
  updatedAt: string | null;
};

type Props = {
  masters: TailorMaster[];
  busy: boolean;
  label: string;
  heading: string;
  untitled: string;
  onPick: (masterId: string) => void;
};

/**
 * "Tailor for this role", with the choice of which master it forks from.
 *
 * Before this, the button took `resumes.find(r => r.kind === "master")` —
 * whichever master the list happened to return first. Someone keeping a
 * backend master and a new-grad master got one of them, silently, with no
 * way to say which, and no way to tell afterwards which it had used.
 *
 * The menu opens on hover and pins on click, the same behaviour as the
 * filter bar's facets, so the two menus on the site work alike. It opens
 * even when there is only one master: the point is that the button now
 * always says what it is about to fork from.
 */
export default function TailorMenu({
  masters,
  busy,
  label,
  heading,
  untitled,
  onPick,
}: Props) {
  const locale = useLocale();
  const [open, setOpen] = useState(false);
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

  function when(iso: string | null): string | null {
    if (!iso) return null;
    try {
      return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(
        new Date(iso),
      );
    } catch {
      return null;
    }
  }

  return (
    <span
      className="jobs-tailor"
      data-open={open ? "1" : "0"}
      ref={box}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="jobs-action"
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={busy}
        onClick={() => setOpen((current) => !current)}
      >
        {label}
        <span className="jobs-action-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      <div className="jobs-tailor-menu" role="menu">
        <h3>{heading}</h3>
        {masters.map((master) => {
          const date = when(master.updatedAt);
          return (
            <button
              key={master.id}
              type="button"
              role="menuitem"
              className="jobs-tailor-opt"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                onPick(master.id);
              }}
            >
              {master.title?.trim() ? master.title : untitled}
              {date ? <span className="jobs-tailor-when">{date}</span> : null}
            </button>
          );
        })}
      </div>
    </span>
  );
}
