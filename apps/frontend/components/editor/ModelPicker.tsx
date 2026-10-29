"use client";

import { Check, Lock, Sparkles } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ModelRow, ModelTable } from "@/lib/api";

/**
 * Which model the next turn runs on, chosen from the composer.
 *
 * In the composer row rather than in the settings panel, deliberately. The
 * choice is per-turn in its consequences -- it decides what this message costs
 * and how good the answer is -- so it belongs where the message is written,
 * next to the attach and send buttons. Burying it in a panel would make it a
 * thing someone sets once and forgets, which is the opposite of what the
 * per-model allowance makes it.
 *
 * Every label the picker shows comes from the caller, as with every other
 * control in this panel: the shell owns the copy and this owns the behaviour.
 *
 * Locked rows are rendered, not hidden. A guest is restricted to the cheaper
 * models (see the backend's ModelSpec.min_tier -- their cap counts turns, so
 * an expensive model would not be noticed by it), and hiding the rest would
 * leave them unable to discover that signing in unlocks them. That discovery
 * is most of what this control is for on the guest tier.
 */

export type ModelPickerLabels = {
  /** On the trigger, for screen readers and the tooltip. */
  title: string;
  /** Heading above the list. */
  heading: string;
  /** The relative-cost chip, e.g. "1x" / "5x". */
  cost: (row: ModelRow) => string;
  /** Why a locked row is locked, and what unlocks it. */
  locked: (row: ModelRow) => string;
  /** Marks the row that would be used if nothing were chosen. */
  isDefault: string;
  /** Link text to the page explaining the whole table. */
  learnMore: string;
};

type Props = {
  table: ModelTable | null;
  labels: ModelPickerLabels;
  disabled?: boolean;
  onSelect: (model: string) => void;
  /** Rendered at the foot of the menu; the shell supplies the link. */
  footer?: React.ReactNode;
};

export default function ModelPicker({
  table,
  labels,
  disabled,
  onSelect,
  footer,
}: Props) {
  // No table means the catalogue fetch failed. The composer still works --
  // the server falls back to this caller's stored choice or the tier default,
  // so a missing picker costs the choice, not the turn -- and showing a
  // control with nothing in it would be worse than showing none.
  if (!table || table.models.length === 0) return null;

  const current =
    table.models.find((row) => row.id === table.selected) ?? table.models[0];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="chat-composer-model"
          disabled={disabled}
          aria-label={`${labels.title}: ${current.label}`}
          title={labels.title}
          data-testid="model-picker"
        >
          <Sparkles className="size-[0.95rem] shrink-0" aria-hidden="true" />
          <span className="chat-composer-model-name">{current.label}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[17rem]">
        <DropdownMenuLabel>{labels.heading}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {table.models.map((row) => {
          const subtitle = row.available
            ? row.id === table.default
              ? labels.isDefault
              : null
            : labels.locked(row);
          return (
            <DropdownMenuItem
              key={row.id}
              // Radix would still fire onSelect on a disabled item in some
              // keyboard paths; the guard in the handler is what actually
              // stops a locked model being chosen, and the server refuses it
              // anyway with a 400.
              disabled={!row.available}
              onSelect={() => {
                if (!row.available || row.id === table.selected) return;
                onSelect(row.id);
              }}
              className="flex-col items-start gap-0.5"
              data-testid={`model-option-${row.id}`}
            >
              <span className="flex w-full items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-medium">
                  {row.label}
                </span>
                {row.id === table.selected ? (
                  <Check className="size-3.5 shrink-0" aria-hidden="true" />
                ) : null}
                {!row.available ? (
                  <Lock className="size-3.5 shrink-0" aria-hidden="true" />
                ) : null}
                <span className="chat-composer-model-cost">
                  {labels.cost(row)}
                </span>
              </span>
              {/* Only when there is something to say. An always-rendered
                  subtitle was fine while every row carried an edits estimate;
                  with that gone most rows have no second line at all. */}
              {subtitle ? (
                <span className="text-xs leading-snug text-muted-foreground">
                  {subtitle}
                </span>
              ) : null}
            </DropdownMenuItem>
          );
        })}
        {footer ? (
          <>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5 text-xs text-muted-foreground">{footer}</div>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
