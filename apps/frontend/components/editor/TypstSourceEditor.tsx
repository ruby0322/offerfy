"use client";

import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { autocompletion } from "@codemirror/autocomplete";
import { indentWithTab } from "@codemirror/commands";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { typst_lezer } from "codemirror-lang-typst/lezer";
import { useTheme } from "next-themes";
import { NARROW_SCREEN, useMediaQuery } from "@/lib/use-media-query";

export type TypstSourceEditorProps = {
  value: string;
  onChange?: (value: string) => void;
  ariaLabel: string;
  lang?: string;
  readOnly?: boolean;
};

function editorTheme(dark: boolean) {
  return EditorView.theme(
    {
      "&": {
        height: "100%",
        backgroundColor: "var(--sheet)",
        color: "var(--ink)",
        fontSize: "var(--typst-font-size, 0.85rem)",
      },
      "&.cm-focused": { outline: "none" },
      ".cm-scroller": {
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        lineHeight: "1.55",
      },
      ".cm-content": { caretColor: "var(--ink)", padding: "1.15rem 0.25rem" },
      ".cm-gutters": {
        backgroundColor: "var(--paper)",
        color: "var(--muted)",
        border: "none",
      },
      ".cm-activeLine": { backgroundColor: "var(--hover)" },
      ".cm-activeLineGutter": { backgroundColor: "var(--paper)" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--ink)" },
      "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
        backgroundColor: "var(--rule)",
      },
    },
    { dark },
  );
}

function TypstTextareaFallback({
  value,
  onChange,
  ariaLabel,
  lang,
  readOnly,
}: TypstSourceEditorProps) {
  return (
    <textarea
      className="typst-editor"
      spellCheck={false}
      value={value}
      lang={lang}
      readOnly={readOnly}
      onChange={(event) => {
        if (readOnly) return;
        onChange?.(event.target.value);
      }}
      onKeyDown={(event) => {
        if (readOnly || event.key !== "Tab") return;
        event.preventDefault();
        const el = event.currentTarget;
        const start = el.selectionStart;
        const end = el.selectionEnd;
        if (event.shiftKey) {
          const before = value.slice(0, start);
          const cut = before.endsWith("  ") ? 2 : before.endsWith("\t") ? 1 : 0;
          if (!cut) return;
          onChange?.(before.slice(0, -cut) + value.slice(end));
          queueMicrotask(() => {
            el.selectionStart = el.selectionEnd = start - cut;
          });
          return;
        }
        onChange?.(value.slice(0, start) + "  " + value.slice(end));
        queueMicrotask(() => {
          el.selectionStart = el.selectionEnd = start + 2;
        });
      }}
      aria-label={ariaLabel}
    />
  );
}

class EditorErrorBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) return this.props.fallback;
    return this.props.children;
  }
}

function TypstCodeMirror({ value, onChange, ariaLabel, lang, readOnly }: TypstSourceEditorProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const themeCompartmentRef = useRef<Compartment | null>(null);
  const wrapCompartmentRef = useRef<Compartment | null>(null);
  const onChangeRef = useRef(onChange);
  const [failed, setFailed] = useState(false);
  const [initialDoc] = useState(value);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  // Sideways scrolling inside a one-column pane is worse than a wrapped line,
  // so narrow screens wrap and wide ones keep the one-line-per-line view.
  const wrap = useMediaQuery(NARROW_SCREEN);

  const wrapRef = useRef(wrap);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    wrapRef.current = wrap;
  }, [wrap]);

  useEffect(() => {
    const parent = parentRef.current;
    if (!parent) return undefined;

    let view: EditorView | null = null;
    let observer: ResizeObserver | null = null;
    let failHandle = 0;
    const themeCompartment = new Compartment();
    themeCompartmentRef.current = themeCompartment;
    const wrapCompartment = new Compartment();
    wrapCompartmentRef.current = wrapCompartment;

    try {
      view = new EditorView({
        parent,
        state: EditorState.create({
          doc: initialDoc,
          extensions: [
            basicSetup,
            keymap.of([indentWithTab]),
            typst_lezer(),
            autocompletion(),
            themeCompartment.of(editorTheme(isDark)),
            wrapCompartment.of(wrapRef.current ? EditorView.lineWrapping : []),
            EditorState.readOnly.of(Boolean(readOnly)),
            EditorView.contentAttributes.of({
              "aria-label": ariaLabel,
              ...(lang ? { lang } : {}),
            }),
            EditorView.updateListener.of((update) => {
              if (readOnly || !update.docChanged) return;
              onChangeRef.current?.(update.state.doc.toString());
            }),
          ],
        }),
      });
      viewRef.current = view;
      observer = new ResizeObserver(() => view?.requestMeasure());
      observer.observe(parent);
    } catch {
      failHandle = window.setTimeout(() => setFailed(true), 0);
    }

    return () => {
      window.clearTimeout(failHandle);
      observer?.disconnect();
      view?.destroy();
      viewRef.current = null;
      themeCompartmentRef.current = null;
      wrapCompartmentRef.current = null;
    };
    // Theme is reconfigured separately so the editor does not remount on toggle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ariaLabel, initialDoc, lang, readOnly]);

  useEffect(() => {
    const view = viewRef.current;
    const compartment = themeCompartmentRef.current;
    if (!view || !compartment) return;
    view.dispatch({
      effects: compartment.reconfigure(editorTheme(isDark)),
    });
  }, [isDark]);

  // Reconfigured rather than passed as a dep of the setup effect: a remount here
  // would rebuild the view from the doc it first opened with.
  useEffect(() => {
    const view = viewRef.current;
    const compartment = wrapCompartmentRef.current;
    if (!view || !compartment) return;
    view.dispatch({
      effects: compartment.reconfigure(wrap ? EditorView.lineWrapping : []),
    });
  }, [wrap]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  if (failed) {
    return (
      <TypstTextareaFallback
        value={value}
        onChange={onChange}
        ariaLabel={ariaLabel}
        lang={lang}
      />
    );
  }

  return <div ref={parentRef} className="typst-cm-host" />;
}

export default function TypstSourceEditor(props: TypstSourceEditorProps) {
  return (
    <EditorErrorBoundary fallback={<TypstTextareaFallback {...props} />}>
      <TypstCodeMirror {...props} />
    </EditorErrorBoundary>
  );
}
