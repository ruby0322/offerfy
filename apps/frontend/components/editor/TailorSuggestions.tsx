"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import EditDiff from "@/components/editor/EditDiff";
import { lineDiff } from "@/lib/edit-diff";
import { applyAction, applyState, type TailorAction, type TailorActionList } from "@/lib/tailor-actions";

/**
 * What the agent would change, each with a before/after preview and a button
 * that applies it.
 *
 * The button does the edit **in the browser**: one search and replace on the
 * draft, through the same source writer the edit card's 重新套用 already uses.
 * No model call, nothing to pay for, no waiting -- which is the whole reason
 * the tool is made to supply an exact `before` rather than a description.
 *
 * Whether a suggestion can still be applied is recomputed from the live
 * source on every render, never read from the chat row: the row is immutable
 * JSON written when the model proposed the change, so after one apply, one
 * typed turn or one hand edit its own `appliable` flag is about a document
 * that no longer exists. `applyState` is that recomputation, and the reason
 * an applied suggestion still reads as applied after a reload with no new
 * column anywhere.
 */
export type TailorCardActions = {
  /** A write is refused right now: a run owns the source, or a turn is live. */
  disabled: boolean;
  /** Takes the whole next document, like `onRestoreEdit`. */
  onApply: (next: string) => void;
  /** Hand the suggestion to the agent instead, quoting it in full. */
  onAsk: (action: TailorAction) => void;
};

function SuggestionRow({
  action,
  currentSource,
  actions,
}: {
  action: TailorAction;
  currentSource: string;
  actions: TailorCardActions;
}) {
  const t = useTranslations("editor");
  const state = applyState(currentSource, action.before, action.after);
  const lines = lineDiff(action.before, action.after);

  // Applying is offered only for a span found exactly once in the document as
  // it stands. Every other state gets the agent instead -- and `onAsk` has to
  // quote the suggestion in full, because a tool row is never replayed to the
  // model, so it has no memory of having proposed this.
  const button =
    state === "appliable" ? (
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-6 px-2 text-[11px]"
        data-testid={`tailor-apply-${action.id}`}
        disabled={actions.disabled}
        onClick={() => {
          const next = applyAction(currentSource, action.before, action.after);
          if (next !== null) actions.onApply(next);
        }}
      >
        {t("actionApply")}
      </Button>
    ) : state === "applied" ? (
      <span className="text-[11px] text-success">{t("actionApplied")}</span>
    ) : state === "noop" ? null : (
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-6 px-2 text-[11px]"
        disabled={actions.disabled}
        onClick={() => actions.onAsk(action)}
      >
        {t("actionAskAgent")}
      </Button>
    );

  return (
    <div className="rounded-md border border-rule px-3 py-2">
      <p className="text-xs font-medium text-foreground">{action.title}</p>
      {action.where || action.requirement ? (
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {[action.where, action.requirement].filter(Boolean).join("　")}
        </p>
      ) : null}
      {action.why ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{action.why}</p>
      ) : null}
      {/* The same diff component the edit card uses, so an applied
          suggestion and an agent edit look the same in the transcript. */}
      {lines.length > 0 ? (
        <div className="mt-1.5">
          <EditDiff lines={lines} action={button} />
        </div>
      ) : button ? (
        <div className="mt-1.5 flex">
          <div className="ml-auto">{button}</div>
        </div>
      ) : null}
      {state === "ambiguous" || state === "missing" ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{t("actionSourceMoved")}</p>
      ) : null}
    </div>
  );
}

export default function TailorSuggestions({
  list,
  currentSource,
  actions,
}: {
  list: TailorActionList;
  currentSource: string;
  actions?: TailorCardActions;
}) {
  const t = useTranslations("editor");
  if (!actions) return null;
  return (
    <div className="mt-2 pl-8">
      <p className="text-xs text-muted-foreground">{t("actionSummary", { count: list.total })}</p>
      <div className="mt-2 flex flex-col gap-2">
        {list.items.map((action) => (
          <SuggestionRow
            key={action.id}
            action={action}
            currentSource={currentSource}
            actions={actions}
          />
        ))}
      </div>
    </div>
  );
}
