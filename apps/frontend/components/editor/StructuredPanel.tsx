"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import RichLine from "@/components/editor/RichLine";
import { ApiError, getProjection, type EditResult, type IntentBody } from "@/lib/api";
import { argLabelId } from "@/lib/arg-labels";
import { shellNeedsResync } from "@/lib/field-ownership";
import { armedUnitIds } from "@/lib/preview-highlight";
import type { Mark } from "@/lib/rich-marks";
import {
  armAfterAdopt,
  armValid,
  blockKey,
  blockLabel,
  canAddBullet,
  canCopyItem,
  canDeleteBlock,
  canDeleteSection,
  canInsertSection,
  deleteImpact,
  documentKey,
  layoutKey,
  sectionDeleteImpact,
  sectionKey,
  sectionMoves,
} from "@/lib/structural-controls";
import {
  argIntent,
  argUnits,
  editRefusalOf,
  hasEditableFields,
  lineIntent,
  unitView,
  type EditRefusal,
  type Projection,
  type ProjectionBlock,
  type ProjectionSection,
  type ProjectionUnit,
} from "@/lib/structured-fields";

/** Commit a field the person has stopped typing in, matching `RichLine`. */
const IDLE_COMMIT_MS = 2000;

/**
 * The look of a per-block action: text weight, no fill. `text-base md:text-sm` keeps a
 * focusable control at 16px on a phone, and `h-auto` lets a long zh-TW label wrap rather
 * than burst a fixed height.
 */
const QUIET = "h-auto whitespace-normal px-2 py-1 text-base text-muted-foreground md:text-sm";

/** The reorder arrows: a 44px target, because these move a whole section. */
const QUIET_TOUCH = "h-11 min-w-11 px-2 text-base text-muted-foreground md:text-sm";

/** A confirm tapped this soon after arming is ignored: it is the same tap, doubled. */
const CONFIRM_GUARD_MS = 400;

/** Which (résumé, source) a projection on screen belongs to. */
const seen = (id: string, src: string) => `${id}\u0000${src}`;

type Props = {
  resumeId: string;
  /**
   * The shell's source. A change the panel did not make -- a chat turn, an import, the
   * history panel -- refetches the projection, because every unit id and offset in it
   * describes the document as it was.
   */
  source: string;
  /**
   * Queue one intent, adopt its answer into the shell.
   *
   * The shell owns the queue and the request rather than this component, because both
   * have to outlive the panel: a commit can still be in flight when the person
   * switches tabs and unmounts it, the shell's 400ms autosave must stay silent until
   * that answer has been adopted, and a queue that died here would let the next mount
   * race the commit still settling from this one.
   *
   * `build` is called when the commit's turn comes, and is handed the projection as it
   * is **then** -- the answer to the commit ahead of it. Null from it, or null back,
   * means there was nothing left to send.
   */
  applyEdit: (build: (projection: Projection) => IntentBody | null) => Promise<EditResult | null>;
  /**
   * Hand the queue a projection this panel fetched, so the next commit is built from
   * it. The queue publishes the ones that come back from `/edit` itself.
   */
  publishProjection: (projection: Projection) => void;
  /**
   * The line unit the caret is in, or null once it has left. The preview marks that
   * unit's lines; the shell owns the id because the preview is not a child of this panel.
   */
  onFocusUnit: (unitId: string | null) => void;
  /**
   * The unit ids an armed delete would take, `[]` when nothing is armed. Reported on
   * every change -- the arm clearing is as much a change as it being set -- and as `[]`
   * when the panel unmounts, so a danger highlight cannot outlive the button that
   * explains it.
   */
  onArmedUnits: (unitIds: string[]) => void;
  /**
   * Re-read the résumé into the shell's buffer.
   *
   * Asked for when this panel's own refetch is not enough: a timed-out edit, whose fate
   * is unknowable from here, and a `stale_hash` from a writer outside this page. In both
   * the shell's buffer is of a document the server no longer has, and the Typst tab's
   * first keystroke would `PUT` it back over whatever did land.
   */
  onResyncSource: () => void;
};

function lookup(
  projection: Projection,
  id: string | null | undefined,
): ProjectionUnit | null {
  if (!id) return null;
  return projection.units[id] ?? null;
}

/**
 * One string argument: a plain `<input>`, never a rich surface.
 *
 * `title: "*x*"` prints the asterisks in the PDF -- a string argument cannot hold
 * formatting -- so this row is deliberately not something the selection popover can
 * reach, and it says so rather than letting someone try.
 */
function ArgRow({
  unit,
  hintId,
  label,
  readOnlyReason,
  notice,
  epoch,
  onCommit,
  onStale,
  onFocusUnit,
}: {
  unit: ProjectionUnit;
  hintId: string;
  /** What the field is called: the translated label, or the raw key when it has none. */
  label: string;
  readOnlyReason: string | null;
  notice?: string;
  /** Changes whenever the unit set does; see the effect below. */
  epoch: string;
  onCommit: (unitId: string, value: string) => void;
  /** A draft typed under an earlier `epoch` was dropped instead of sent. */
  onStale: (unitId: string) => void;
  /**
   * The caret arrived in this field, or left it, so the preview can mark where it
   * prints. An argument carries no label of its own, so its band is the rows of its
   * block (`typst_anchors._arg_bands`); a block whose rows cannot be bracketed has no
   * band and nothing is drawn, which is the honest answer rather than a guessed one.
   */
  onFocusUnit: (unitId: string | null) => void;
}) {
  const inputId = useId();
  const ref = useRef<HTMLInputElement | null>(null);
  const idleRef = useRef<number | null>(null);
  const [draft, setDraft] = useState(unit.value);
  /** Typed under an `epoch` that has since changed; see the epoch effect. */
  const staleRef = useRef(false);
  const latest = useRef({ draft, value: unit.value, onCommit, onStale, id: unit.id });

  useEffect(() => {
    latest.current = { draft, value: unit.value, onCommit, onStale, id: unit.id };
  });

  useEffect(() => {
    // Never overwrite what the person is in the middle of typing: an idle commit comes
    // back with the server's own copy of this value while the caret is still here.
    if (document.activeElement === ref.current) return;
    setDraft(unit.value);
  }, [unit.value]);

  const clearIdle = useCallback(() => {
    if (idleRef.current === null) return;
    window.clearTimeout(idleRef.current);
    idleRef.current = null;
  }, []);

  const epochRef = useRef(epoch);
  useEffect(() => {
    // The unit set changed (a block added or removed, a section moved), so this id may
    // now name another part of the document. A draft that survived a refused commit
    // would be sent against it on the next blur; drop it and show what the document
    // holds. Not while the caret is here: that text is the person's, and the server's
    // `expect` check refuses it rather than writing it to the wrong place.
    if (epochRef.current === epoch) return;
    epochRef.current = epoch;
    if (document.activeElement === ref.current && latest.current.draft !== latest.current.value) {
      // Typed text is here, so it is the person's and is not yanked from under the caret.
      // But it was typed against ids that have since moved, so it is never sent: the
      // next commit drops it and says why. (`expect` cannot be that net -- the queue
      // builds it from the projection the server just returned, so it always matches.)
      staleRef.current = true;
      return;
    }
    clearIdle();
    staleRef.current = false;
    setDraft(unit.value);
  }, [epoch, clearIdle, unit.value]);

  const commit = useCallback(
    (next: string) => {
      clearIdle();
      const now = latest.current;
      if (staleRef.current) {
        staleRef.current = false;
        setDraft(now.value);
        now.onStale(now.id);
        return;
      }
      // A cheap early-out only: `value` is this render's projection, which is one
      // answer behind while a commit is in flight. The panel rebuilds the intent
      // against the freshest projection and drops it if there is nothing left to send.
      if (next === now.value) return;
      now.onCommit(now.id, next);
    },
    [clearIdle],
  );

  useEffect(() => {
    return () => {
      clearIdle();
      // A click on another tab blurs this input first, but a keyboard or programmatic
      // switch need not, and the idle timer dies with the component. Sending the same
      // value twice costs nothing: the panel's queue resolves the second one against
      // the answer to the first and finds nothing to do.
      const now = latest.current;
      if (now.draft !== now.value && !staleRef.current) now.onCommit(now.id, now.draft);
    };
  }, [clearIdle]);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <label htmlFor={inputId} className="min-w-20 shrink-0 text-xs text-muted-foreground">
        {label}
      </label>
      <input
        id={inputId}
        ref={ref}
        // `rr-input` carries no font-size, so `text-base md:text-sm` decides it: under
        // 16px iOS zooms into the field and never zooms back out.
        className="rr-input min-w-0 flex-1 basis-48 text-base md:text-sm"
        value={draft}
        readOnly={!!readOnlyReason}
        aria-describedby={readOnlyReason ? undefined : hintId}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          clearIdle();
          idleRef.current = window.setTimeout(() => {
            idleRef.current = null;
            commit(next);
          }, IDLE_COMMIT_MS);
        }}
        onFocus={() => onFocusUnit(unit.id)}
        onBlur={() => {
          onFocusUnit(null);
          commit(draft);
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          commit(draft);
        }}
      />
      {readOnlyReason ? (
        <p className="basis-full text-xs text-muted-foreground">{readOnlyReason}</p>
      ) : null}
      {notice ? <p className="basis-full text-xs text-clay">{notice}</p> : null}
    </div>
  );
}

/**
 * A delete in two clicks: the first arms it and says what goes, the second does it.
 * Shared by the block and the section delete, so the two cannot drift apart.
 *
 * The count is of the content in the fields, in characters -- bytes mean nothing to the
 * person this is for. `armedAt` is null while unarmed; the panel decides what "armed"
 * means (the arm holds the target's key, and is dropped outright whenever a projection
 * this panel did not produce is adopted -- a swap of two same-shaped entries has an
 * identical key).
 *
 * Armed, the safe button is where the finger just was: 取消 comes first and the
 * destructive one second, and a confirm in the first 400 ms is ignored, so the reflexive
 * second tap on a phone that seemed not to register the first deletes nothing. The region
 * is a polite live region, so a screen reader is told what the count is.
 */
function ArmedDelete({
  armedAt,
  busy,
  label,
  ariaLabel,
  impact,
  note,
  onArm,
  onCancel,
  onConfirm,
}: {
  armedAt: number | null;
  busy: boolean;
  label: string;
  ariaLabel: string;
  impact: { units: number; chars: number };
  note?: string;
  onArm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useTranslations("editor");
  return (
    <div aria-live="polite" className="flex flex-col items-start gap-1">
      {armedAt === null ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className={QUIET}
          aria-label={ariaLabel}
          disabled={busy}
          onClick={onArm}
        >
          {label}
        </Button>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1">
            <Button type="button" size="sm" variant="ghost" className={QUIET} onClick={onCancel}>
              {t("fieldsDeleteCancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-auto whitespace-normal border-clay px-2 py-1 text-base text-clay md:text-sm"
              disabled={busy}
              onClick={() => {
                if (Date.now() - armedAt < CONFIRM_GUARD_MS) return;
                onConfirm();
              }}
            >
              {t("fieldsDeleteConfirm", { units: impact.units, chars: impact.chars })}
            </Button>
          </div>
          {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
        </>
      )}
    </div>
  );
}

/**
 * The fields tab: sections, blocks, and one editing surface per value.
 *
 * Everything the person changes goes out as an intent and comes back as new source --
 * this component never writes a byte of Typst, and never touches `source` except
 * through `applyEdit`.
 */
export default function StructuredPanel({
  resumeId,
  source,
  applyEdit,
  publishProjection,
  onFocusUnit,
  onArmedUnits,
  onResyncSource,
}: Props) {
  const t = useTranslations("editor");
  const [projection, setProjection] = useState<Projection | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  /**
   * A refusal, against the field it was about.
   *
   * Per unit, not one slot for the panel: a `compile_failed` leaves text on screen that
   * the document does not contain, and a panel-wide notice would be cleared by the next
   * edit of any other field -- the divergence outliving the only thing that explained
   * it. This one stays until that field's own next commit succeeds.
   */
  const [unitNotices, setUnitNotices] = useState<Record<string, string>>({});
  const [inFlight, setInFlight] = useState(0);
  /**
   * A delete the person has clicked once. `key` is `blockKey` at that moment: positional
   * ids mean `b3` is only the same block while the document's shape is, and a value edit
   * (a blurred field committing) must not disarm it, so a hash would be the wrong thing
   * to hold. Valid only while `blockKey` still equals it; see `structural-controls.ts`.
   */
  const [armed, setArmed] = useState<{ target: string; key: string; at: number } | null>(
    null,
  );
  /**
   * One structural edit at a time. A value commit never changes the unit set so any
   * number may queue; a structural one renumbers, so a second click aimed at ids read
   * from the render before it would land on whatever moved into them. Disabling the
   * controls is the UI half; the ref is the half that cannot be raced by a re-render.
   */
  const [structuralBusy, setStructuralBusy] = useState(false);
  /** Bumped by every structural success; part of `epoch`. */
  const [structuralEpoch, setStructuralEpoch] = useState(0);
  /** Bumped when the panel adopts a projection it fetched rather than produced. */
  const [externalEpoch, setExternalEpoch] = useState(0);
  /**
   * Changes when the unit set does, by any route: this panel's structural edit, or a
   * rewrite from the chat, the history panel or the Typst tab. Rows drop a draft left
   * over from before it, because the id it was typed under may now name something else.
   */
  const epoch = useMemo(
    () =>
      `${structuralEpoch}.${externalEpoch}|${projection ? layoutKey(projection) : ""}`,
    [structuralEpoch, externalEpoch, projection],
  );
  /**
   * What the armed delete would take, recomputed from the projection and the arm every
   * time either changes -- `armedUnitIds` checks the arm's key still matches, so an arm
   * the next render would not draw cannot be reported either. Never held anywhere else.
   */
  const armedUnits = useMemo(() => armedUnitIds(projection, armed), [projection, armed]);
  useLayoutEffect(() => {
    onArmedUnits(armedUnits);
  }, [armedUnits, onArmedUnits]);
  // The focused id is positional too: when the unit set changes, the id the caret was
  // reported under may name another line, so the highlight is dropped rather than moved.
  useEffect(() => {
    onFocusUnit(null);
  }, [epoch, onFocusUnit]);
  // Leaving the tab unmounts the panel, and with it the delete button that explained the
  // red lines on the page; so the caret and the arm both go with it.
  useEffect(
    () => () => {
      onArmedUnits([]);
      onFocusUnit(null);
    },
    [onArmedUnits, onFocusUnit],
  );
  const structuralRef = useRef(false);
  const [reloads, setReloads] = useState(0);
  /**
   * Which (résumé, source) the projection on screen describes, so our own edits do not
   * refetch. The résumé id is part of it because `onSwitchResume` carries the tab over:
   * this panel can render with a new id and the previous résumé's `source` for a frame,
   * and offering someone else's form -- even briefly -- must not be possible.
   */
  const haveFor = useRef<string | null>(null);

  const reload = useCallback(() => {
    haveFor.current = null;
    setReloads((n) => n + 1);
  }, []);

  const adopt = useCallback(
    (next: Projection, forSource: string, produced: boolean) => {
      // The queue publishes the projections that come back from `/edit` itself, in the
      // same turn as the answer; this covers the ones this panel fetched, and is
      // harmlessly idempotent for the others.
      publishProjection(next);
      setProjection(next);
      setLoadFailed(false);
      haveFor.current = forSource;
      // `produced` is the only guard against the one thing no key can see: two sibling
      // entries of the same shape that swapped places have an identical key, so an arm
      // would confirm against the other one. See `armAfterAdopt`.
      setArmed((prev) => armAfterAdopt(prev, { produced }));
    },
    [publishProjection],
  );

  useEffect(() => {
    if (haveFor.current === seen(resumeId, source)) return;
    let cancelled = false;
    getProjection(resumeId)
      .then((next) => {
        if (cancelled) return;
        // A projection this panel did not produce: a chat turn, a history restore, the
        // Typst tab. It may be the same document with two sibling entries swapped, in
        // which case every id, shape and key is identical and only the values moved --
        // and an armed delete would then confirm against the other entry. Nothing
        // computed from the projection can tell, so `adopt` is told it did not produce
        // this one and drops the arm; rows drop drafts typed against the old one.
        setExternalEpoch((n) => n + 1);
        adopt(next, seen(resumeId, source), false);
      })
      .catch(() => {
        // A transport failure, a 404 from an API older than this page, or a body that
        // is not a projection. None of them means "this resume has no editable
        // fields", so none of them renders as that.
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [adopt, resumeId, source, reloads]);

  const noteRefusal = useCallback((unitId: string, message: string) => {
    setUnitNotices((prev) => ({ ...prev, [unitId]: message }));
  }, []);

  const noteStale = useCallback(
    (unitId: string) => noteRefusal(unitId, t("fieldsDraftStale")),
    [noteRefusal, t],
  );

  const send = useCallback(
    async (
      unitId: string,
      build: (projection: Projection) => IntentBody | null,
      /**
       * `structural`: the answer renumbers the units, so every note keyed by a unit id
       * now describes a different part of the document and goes. `refused` words an
       * `unsupported` refusal in this control's own terms.
       */
      opts: { structural?: boolean; refused?: (refusal: EditRefusal) => string } = {},
    ): Promise<void> => {
      setNotice(null);
      setInFlight((n) => n + 1);
      // What the shell's buffer was when this was queued, to tell a `stale_hash`
      // caused by this editor itself from one caused by somewhere else.
      const from = haveFor.current;
      try {
        // `build` is called when the queue reaches this commit, with the projection the
        // answer before it carried -- so the hash and the `expect` are the ones the
        // server actually holds. The queue owns that, not this component.
        const out = await applyEdit(build);
        // Nothing was sent: the commit ahead of this one already carried the same
        // text, or the value was put back while this waited.
        if (!out) return;
        adopt(out.projection, seen(resumeId, out.typstSource), true);
        if (opts.structural) {
          // Ids were renumbered under every note and every arm.
          setUnitNotices({});
          setArmed(null);
          setStructuralEpoch((n) => n + 1);
        } else {
          setUnitNotices((prev) => {
            if (!(unitId in prev)) return prev;
            const next = { ...prev };
            delete next[unitId];
            return next;
          });
        }
      } catch (err) {
        const refusal = err instanceof ApiError ? editRefusalOf(err.body) : null;
        const aborted = err instanceof Error && err.name === "AbortError";
        // The queue means a staleness refusal is never one of our own commits colliding
        // with another. It is either this editor writing the whole document from
        // somewhere else -- the Typst tab's autosave, a chat turn, a restore, which all
        // move the shell's buffer and so `haveFor` -- or a writer outside this page
        // entirely. Those are different sentences to read, and only the second leaves
        // the shell's buffer describing a document the server no longer has.
        const writtenHere = haveFor.current !== from;
        if (shellNeedsResync({ aborted, code: refusal?.code ?? null, writtenHere })) {
          // Reloading this panel's projection is not enough: the Typst tab would still
          // show the pre-edit document and its first keystroke would PUT it back over
          // whatever did land.
          onResyncSource();
        }
        if (aborted) {
          // The shell gave up waiting. Whether the server applied it is unknowable
          // from here, so say so and show what the document actually holds.
          noteRefusal(unitId, t("fieldsEditTimeout"));
          reload();
        } else if (refusal?.code === "stale_hash" || refusal?.code === "expect_mismatch") {
          const message = writtenHere ? t("fieldsStaleHere") : t("fieldsStale");
          setNotice(message);
          noteRefusal(unitId, message);
          reload();
        } else if (refusal?.code === "compile_failed") {
          noteRefusal(unitId, t("fieldsCompileFailed", { detail: refusal.detail }));
        } else if (refusal && opts.refused) {
          noteRefusal(unitId, opts.refused(refusal));
        } else if (refusal) {
          noteRefusal(unitId, t("fieldsUnsupported", { detail: refusal.detail }));
        } else {
          noteRefusal(unitId, t("fieldsSaveError"));
        }
      } finally {
        setInFlight((n) => n - 1);
      }
    },
    [adopt, applyEdit, noteRefusal, onResyncSource, reload, resumeId, t],
  );

  const commitArg = useCallback(
    (unitId: string, value: string): void => {
      void send(unitId, (p) => argIntent(p, unitId, value));
    },
    [send],
  );

  const commitLine = useCallback(
    (unitId: string, next: { text: string; marks: Mark[] }): void => {
      void send(unitId, (p) => lineIntent(p, unitId, next));
    },
    [send],
  );

  /**
   * Send one structural intent, at most one at a time.
   *
   * It goes through `send`, hence the shell's queue, like every value commit -- there is
   * no second path. What is specific to it: `expect` is the target's key as the control
   * was drawn, and the intent is built (when the queue's turn comes, from the queue's
   * projection) only if the target still has that key. Anything else means the ids this
   * control holds are not the ones the document has now, and sending would act on
   * whatever moved into them, so nothing is sent.
   */
  const structural = useCallback(
    (spec: {
      noticeKey: string;
      expect: string | null;
      keyOf: (projection: Projection) => string | null;
      intent: (projection: Projection) => IntentBody;
      refused?: (refusal: EditRefusal) => string;
    }): void => {
      if (structuralRef.current || spec.expect === null) return;
      structuralRef.current = true;
      setStructuralBusy(true);
      setArmed(null);
      setUnitNotices((prev) => {
        if (!(spec.noticeKey in prev)) return prev;
        const next = { ...prev };
        delete next[spec.noticeKey];
        return next;
      });
      void send(
        spec.noticeKey,
        (p) => (spec.keyOf(p) === spec.expect ? spec.intent(p) : null),
        {
          structural: true,
          refused:
            spec.refused ??
            ((refusal) => t("fieldsStructRefused", { detail: refusal.detail })),
        },
      ).finally(() => {
        structuralRef.current = false;
        setStructuralBusy(false);
      });
    },
    [send, t],
  );

  /**
   * The words a new entry is written with, in the person's own language.
   *
   * They are content, not Typst, and `apply_intent` has no locale -- so the locale that
   * holds them sends them. Left out, the server writes its CJK defaults, which is how an
   * English-locale résumé ended up with 「（待填）」 in a `url:` argument.
   */
  const words = useCallback(
    () => ({ placeholder: t("fieldsNewPlaceholder"), title: t("fieldsNewSectionTitle") }),
    [t],
  );

  /**
   * Why "add another entry like this section's" was refused, in the person's language.
   *
   * The reason comes from the server (`IntentError.reason`) because only it knows which
   * of the four it was: the projection cannot say which block holds a positional string
   * (`report.skipped_args["(positional)"]` is a document-wide count with no block id),
   * nor whether this document's template has an emitter. An unknown reason falls back to
   * the generic sentence with the server's own detail rather than guessing.
   */
  const copyRefusal = useCallback(
    (refusal: EditRefusal): string => {
      if (refusal.reason === "positional") return t("fieldsCopyRefused");
      if (refusal.reason === "no_emitter") return t("fieldsCopyNoEmitter");
      if (refusal.reason === "template_shape") return t("fieldsCopyTemplate");
      if (refusal.reason === "nothing_to_copy") return t("fieldsCopyNothing");
      return t("fieldsStructRefused", { detail: refusal.detail });
    },
    [t],
  );

  const blockIntent = useCallback(
    (
      projected: Projection,
      target: string,
      kind: "insert_bullet" | "delete",
    ): void => {
      structural({
        noticeKey: `struct:${target}`,
        expect: blockKey(projected, target),
        keyOf: (p) => blockKey(p, target),
        intent: (p) => ({
          hash: p.hash,
          kind,
          target,
          ...(kind === "insert_bullet" ? { placeholder: words().placeholder } : {}),
        }),
      });
    },
    [structural, words],
  );

  const sectionIntent = useCallback(
    (
      projected: Projection,
      target: string,
      kind: "insert_item" | "move_section" | "delete",
      dir?: "up" | "down",
    ): void => {
      structural({
        noticeKey: `struct:${target}`,
        expect: sectionKey(projected, target),
        keyOf: (p) => sectionKey(p, target),
        intent: (p) => ({
          hash: p.hash,
          kind,
          target,
          ...(dir ? { dir } : {}),
          ...(kind === "insert_item" ? { placeholder: words().placeholder } : {}),
        }),
        // `insert_item` is refused for four quite different reasons and the projection
        // cannot say which from here, so the server names it and this picks the
        // sentence. An empty section with no emitter is now the common one (the button
        // is offered there -- that is the whole point of the emitter fallback), and it
        // used to read "this section's entries hold text the form cannot blank out",
        // which is only ever true of the positional case.
        refused: kind === "insert_item" ? copyRefusal : undefined,
      });
    },
    [copyRefusal, structural, words],
  );

  const addSection = useCallback(
    (projected: Projection): void => {
      structural({
        noticeKey: "struct:doc",
        expect: documentKey(projected),
        keyOf: documentKey,
        intent: (p) => ({ hash: p.hash, kind: "insert_section", ...words() }),
      });
    },
    [structural, words],
  );

  const roleLabel = useCallback(
    (role: string | null): string => {
      if (role === "heading") return t("fieldsHeading");
      if (role === "bullet") return t("fieldsBullet");
      return t("fieldsPara");
    },
    [t],
  );

  /** Why this unit cannot be edited, in the person's language, or null. */
  const readOnlyReason = useCallback(
    (unit: ProjectionUnit): string | null => {
      const view = unitView(unit);
      if (view.editable) return null;
      if (view.reason === "kind") return t("fieldsReadOnlyKind");
      if (view.reason === "marks") return t("fieldsReadOnlyMarks");
      return t("fieldsReadOnlySerialize");
    },
    [t],
  );

  /** What an argument's row is called: its translated label, else the raw Typst key. */
  const argLabel = useCallback(
    (unit: ProjectionUnit): string => {
      const id = argLabelId(unit.key);
      return id ? t(id) : (unit.key ?? "");
    },
    [t],
  );

  const line = useCallback(
    (unit: ProjectionUnit) => {
      const view = unitView(unit);
      const reason = readOnlyReason(unit);
      const refused = unitNotices[unit.id];
      return (
        <div
          key={unit.id}
          className="flex flex-col gap-1"
          // Focus events bubble, so this sees the caret arrive in and leave the line
          // (and the format popover beside it). Moving between the two is not leaving.
          onFocus={() => onFocusUnit(unit.id)}
          onBlur={(event) => {
            const to = event.relatedTarget;
            if (to instanceof Node && event.currentTarget.contains(to)) return;
            onFocusUnit(null);
          }}
        >
          <RichLine
            text={unit.value}
            marks={view.marks}
            opaque={unit.opaque}
            readOnly={!view.editable}
            ariaLabel={roleLabel(unit.role)}
            className={unit.role === "heading" ? "font-semibold" : undefined}
            epoch={epoch}
            onStale={() => noteStale(unit.id)}
            onCommit={(next) => commitLine(unit.id, next)}
            onEmptied={() => noteRefusal(unit.id, t("fieldsLineEmpty"))}
          />
          {reason ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
          {/* Stays until this field's own next commit lands: it is the only thing
              saying the line on screen is not the line in the document. */}
          {refused ? <p className="text-xs text-clay">{refused}</p> : null}
        </div>
      );
    },
    [
      commitLine,
      epoch,
      noteRefusal,
      noteStale,
      onFocusUnit,
      readOnlyReason,
      roleLabel,
      t,
      unitNotices,
    ],
  );

  const deleteControl = useCallback(
    (projected: Projection, section: ProjectionSection, raw: ProjectionBlock) => {
      if (!canDeleteBlock(raw)) return null;
      const impact = deleteImpact(projected, section, raw);
      const name = blockLabel(projected, raw);
      const key = blockKey(projected, raw.id);
      return (
        <ArmedDelete
          armedAt={armValid(armed, raw.id, key) ? armed.at : null}
          busy={structuralBusy || key === null}
          label={t("fieldsDelete")}
          ariaLabel={name ? t("fieldsDeleteNamed", { name }) : t("fieldsDelete")}
          impact={impact}
          note={impact.lastInSection ? t("fieldsDeleteLast") : undefined}
          onArm={() => key !== null && setArmed({ target: raw.id, key, at: Date.now() })}
          onCancel={() => setArmed(null)}
          onConfirm={() => blockIntent(projected, raw.id, "delete")}
        />
      );
    },
    [armed, blockIntent, structuralBusy, t],
  );

  /**
   * Delete a whole section: one `delete` intent naming the section. The count is its title
   * and everything the form shows in it. `Section.outer` runs to the next heading, so
   * comments, `#let`s, a `#pagebreak()` or a table go too and cannot be counted -- the
   * projection's report is document-wide -- so the armed state says so instead.
   */
  const deleteSectionControl = useCallback(
    (projected: Projection, section: ProjectionSection) => {
      if (!canDeleteSection(projected, section.id)) return null;
      const name = lookup(projected, section.titleUnit)?.value ?? "";
      const key = sectionKey(projected, section.id);
      return (
        <ArmedDelete
          armedAt={armValid(armed, section.id, key) ? armed.at : null}
          busy={structuralBusy || key === null}
          label={t("fieldsDeleteSection")}
          ariaLabel={
            name ? t("fieldsDeleteSectionNamed", { name }) : t("fieldsDeleteSection")
          }
          impact={sectionDeleteImpact(projected, section)}
          note={t("fieldsDeleteSectionExtra")}
          onArm={() => key !== null && setArmed({ target: section.id, key, at: Date.now() })}
          onCancel={() => setArmed(null)}
          onConfirm={() => sectionIntent(projected, section.id, "delete")}
        />
      );
    },
    [armed, sectionIntent, structuralBusy, t],
  );

  const block = useCallback(
    (projected: Projection, section: ProjectionSection, raw: ProjectionBlock) => {
      const args = argUnits(raw, projected.units);
      const found = (unit: ProjectionUnit | null): unit is ProjectionUnit => unit !== null;
      const bodyLines = raw.bodyUnits.map((id) => lookup(projected, id)).filter(found);
      const bulletRows = raw.bullets
        .map((bullet) => ({ bullet, unit: lookup(projected, bullet.unit) }))
        .filter((row): row is { bullet: ProjectionBlock; unit: ProjectionUnit } => row.unit !== null);
      const own = lookup(projected, raw.unit);
      const hintId = `${raw.id}-arg-hint`;
      const styleHintId = `${raw.id}-style-hint`;
      const refused = unitNotices[`struct:${raw.id}`];

      // A bare paragraph or bullet is one line and nothing else: no card around it.
      if (
        !args.fields.length &&
        !args.style.length &&
        own &&
        !bodyLines.length &&
        !bulletRows.length &&
        !canAddBullet(raw)
      ) {
        return (
          <div key={raw.id} className="flex flex-col gap-1">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">{line(own)}</div>
              {deleteControl(projected, section, raw)}
            </div>
            {refused ? <p className="text-xs text-clay">{refused}</p> : null}
          </div>
        );
      }
      return (
        <div key={raw.id} className="flex flex-col gap-3 rounded-md border border-rule p-3">
          {raw.name ? (
            <p className="font-mono text-xs text-muted-foreground">{raw.name}</p>
          ) : null}
          {args.fields.length ? (
            <div className="flex flex-col gap-2">
              {args.fields.map((unit) => (
                <ArgRow
                  key={unit.id}
                  unit={unit}
                  hintId={hintId}
                  label={argLabel(unit)}
                  readOnlyReason={readOnlyReason(unit)}
                  notice={unitNotices[unit.id]}
                  epoch={epoch}
                  onCommit={commitArg}
                  onStale={noteStale}
                  onFocusUnit={onFocusUnit}
                />
              ))}
              <p id={hintId} className="text-xs text-muted-foreground">
                {t("fieldsArgHint")}
              </p>
            </div>
          ) : null}
          {own ? line(own) : null}
          {bodyLines.map((unit) => line(unit))}
          {bulletRows.map(({ bullet, unit }) => (
            <div key={bullet.id} className="flex flex-col gap-1">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">{line(unit)}</div>
                {deleteControl(projected, section, bullet)}
              </div>
              {unitNotices[`struct:${bullet.id}`] ? (
                <p className="text-xs text-clay">{unitNotices[`struct:${bullet.id}`]}</p>
              ) : null}
            </div>
          ))}
          {args.style.length ? (
            <details className="rounded-md bg-muted/30 px-2 py-1">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                {t("fieldsStyleArgs")}
              </summary>
              <div className="flex flex-col gap-2 py-2">
                {args.style.map((unit) => (
                  <ArgRow
                    key={unit.id}
                    unit={unit}
                    hintId={styleHintId}
                    label={argLabel(unit)}
                    readOnlyReason={readOnlyReason(unit)}
                    notice={unitNotices[unit.id]}
                    epoch={epoch}
                    onCommit={commitArg}
                    onStale={noteStale}
                    onFocusUnit={onFocusUnit}
                  />
                ))}
                <p id={styleHintId} className="text-xs text-muted-foreground">
                  {t("fieldsArgHint")}
                </p>
              </div>
            </details>
          ) : null}
          {/* Quiet, text-weight actions: in zh-TW a row of filled buttons crowds, and
              only the armed delete earns any emphasis. */}
          <div className="flex flex-wrap items-start gap-x-1 gap-y-1">
            {canAddBullet(raw) ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className={QUIET}
                disabled={structuralBusy}
                onClick={() => blockIntent(projected, raw.id, "insert_bullet")}
                aria-label={
                  blockLabel(projected, raw)
                    ? t("fieldsAddBulletNamed", { name: blockLabel(projected, raw) })
                    : undefined
                }
              >
                {t("fieldsAddBullet")}
              </Button>
            ) : null}
            {deleteControl(projected, section, raw)}
          </div>
          {refused ? <p className="text-xs text-clay">{refused}</p> : null}
        </div>
      );
    },
    [
      argLabel,
      blockIntent,
      commitArg,
      epoch,
      deleteControl,
      line,
      noteStale,
      onFocusUnit,
      readOnlyReason,
      structuralBusy,
      t,
      unitNotices,
    ],
  );

  const retry = (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">{t("fieldsLoadError")}</span>
      <Button type="button" size="sm" variant="outline" onClick={reload}>
        {t("fieldsRetry")}
      </Button>
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {notice || inFlight > 0 || (loadFailed && projection) ? (
        <div className="flex flex-col gap-2 border-b border-rule px-3 py-2">
          {inFlight > 0 ? (
            <span className="text-xs text-muted-foreground">{t("fieldsSaving")}</span>
          ) : null}
          {notice ? <span className="text-xs text-clay">{notice}</span> : null}
          {loadFailed && projection ? retry : null}
        </div>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {!projection ? (
          loadFailed ? (
            retry
          ) : (
            <p className="text-sm text-muted-foreground">{t("fieldsLoading")}</p>
          )
        ) : !hasEditableFields(projection) ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm">{t("fieldsEmpty")}</p>
            <p className="text-sm text-muted-foreground">{t("fieldsEmptyHint")}</p>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {projection.sections.map((section) => {
              const title = lookup(projection, section.titleUnit);
              const moves = sectionMoves(projection, section.id);
              const sectionNotice = unitNotices[`struct:${section.id}`];
              return (
                <section key={section.id} className="flex flex-col gap-3">
                  <div className="flex items-start gap-2 border-b border-rule pb-1">
                    <div className="min-w-0 flex-1">
                      {title ? (
                        line(title)
                      ) : (
                        <h3 className="text-sm font-semibold">{t("fieldsPreamble")}</h3>
                      )}
                    </div>
                    {section.titleUnit ? (
                      <div className="flex shrink-0 items-center">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className={QUIET_TOUCH}
                          aria-label={t("fieldsMoveUp")}
                          title={t("fieldsMoveUp")}
                          disabled={structuralBusy || !moves.up}
                          onClick={() => sectionIntent(projection, section.id, "move_section", "up")}
                        >
                          ↑
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className={QUIET_TOUCH}
                          aria-label={t("fieldsMoveDown")}
                          title={t("fieldsMoveDown")}
                          disabled={structuralBusy || !moves.down}
                          onClick={() =>
                            sectionIntent(projection, section.id, "move_section", "down")
                          }
                        >
                          ↓
                        </Button>
                      </div>
                    ) : null}
                  </div>
                  {section.blocks.map((raw) => block(projection, section, raw))}
                  {canCopyItem(section) || canDeleteSection(projection, section.id) ? (
                    <div className="flex flex-wrap items-start gap-x-1 gap-y-1">
                      {canCopyItem(section) ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className={QUIET}
                          disabled={structuralBusy}
                          onClick={() => sectionIntent(projection, section.id, "insert_item")}
                        >
                          {t("fieldsAddItem")}
                        </Button>
                      ) : null}
                      <div className="ml-auto">{deleteSectionControl(projection, section)}</div>
                    </div>
                  ) : null}
                  {sectionNotice ? <p className="text-xs text-clay">{sectionNotice}</p> : null}
                </section>
              );
            })}
            {canInsertSection(projection) ? (
              <div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className={QUIET}
                  disabled={structuralBusy}
                  onClick={() => addSection(projection)}
                >
                  {t("fieldsAddSection")}
                </Button>
                {unitNotices["struct:doc"] ? (
                  <p className="text-xs text-clay">{unitNotices["struct:doc"]}</p>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
