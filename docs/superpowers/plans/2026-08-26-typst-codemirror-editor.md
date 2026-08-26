# Typst CodeMirror Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Typst-tab `<textarea>` with a client-only CodeMirror 6 editor using `typst_lezer()`, keeping React `source` as the source of truth and server-side preview/compile/ATS.

**Architecture:** `TypstSourceEditor` mounts CodeMirror once, reports edits via `onChange`, and applies external `value` only when the document text differs. `EditorShell` keeps existing save/preview/chat timers. Import or init failure falls back to the current textarea.

**Tech Stack:** Next 16.1.6, React 19.2.3, CodeMirror 6 (`codemirror` + `@codemirror/state` + `@codemirror/view` + `@codemirror/autocomplete`), `codemirror-lang-typst` `typst_lezer()` (Typst 0.15, no WASM).

## Global Constraints

- Product name is Offerfy. Never CareerOS, Roleloop, Offerly, Offerloop, or RenderResume in UI copy.
- Editor is not RR smart-chat. Left: two tabs only — Typst editor, AI chat. Right: realtime preview.
- Preview: SVG debounce 400ms on Typst source change. PDF on idle 2s. ATS on successful compile.
- Chat send still PUTs current `source` before POST chat.
- Locales: `en`, `zh-TW`, `zh-CN` only.
- Do not compile Typst in the browser. No Tinymist, Monaco, collab, or dark theme.
- Do not commit unless the user asks.
- Verify UI in the browser before claiming complete.

## File map

- Create: `apps/frontend/components/editor/TypstSourceEditor.tsx` — CodeMirror host, light theme, textarea fallback.
- Modify: `apps/frontend/components/editor/EditorShell.tsx` — swap textarea for dynamic `TypstSourceEditor`.
- Modify: `apps/frontend/app/[locale]/globals.css` — `.typst-cm-host` fills the pane; keep `.typst-editor` for fallback.
- Modify: `apps/frontend/package.json` — add CodeMirror + `codemirror-lang-typst`.

---

### Task 1: TypstSourceEditor

**Files:**
- Create: `apps/frontend/components/editor/TypstSourceEditor.tsx`
- Modify: `apps/frontend/package.json` (and lockfile via npm install)

**Interfaces:**
- Consumes: none
- Produces: default export `TypstSourceEditor(props: { value: string; onChange: (value: string) => void; ariaLabel: string; lang?: string })`

- [ ] **Step 1: Install packages**

Run from `apps/frontend`:

```bash
npm install codemirror @codemirror/state @codemirror/view @codemirror/autocomplete codemirror-lang-typst
```

Expected: packages listed in `package.json` dependencies.

- [ ] **Step 2: Add TypstSourceEditor**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { autocompletion } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { typst_lezer } from "codemirror-lang-typst/lezer";

export type TypstSourceEditorProps = {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  lang?: string;
};

const lightTheme = EditorView.theme(
  {
    "&": {
      height: "100%",
      backgroundColor: "#f8fafc",
      color: "#1e293b",
      fontSize: "0.85rem",
    },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": {
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      lineHeight: "1.55",
    },
    ".cm-content": { caretColor: "#0f172a", padding: "1.15rem 0.25rem" },
    ".cm-gutters": {
      backgroundColor: "#f1f5f9",
      color: "#64748b",
      border: "none",
    },
  },
  { dark: false },
);

function TypstTextareaFallback({ value, onChange, ariaLabel, lang }: TypstSourceEditorProps) {
  return (
    <textarea
      className="typst-editor"
      spellCheck={false}
      value={value}
      lang={lang}
      onChange={(event) => onChange(event.target.value)}
      aria-label={ariaLabel}
    />
  );
}

function TypstCodeMirror({ value, onChange, ariaLabel, lang }: TypstSourceEditorProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const [failed, setFailed] = useState(false);
  onChangeRef.current = onChange;

  useEffect(() => {
    const parent = parentRef.current;
    if (!parent) return;
    try {
      const view = new EditorView({
        parent,
        state: EditorState.create({
          doc: value,
          extensions: [
            basicSetup,
            typst_lezer(),
            autocompletion(),
            lightTheme,
            EditorView.contentAttributes.of({
              "aria-label": ariaLabel,
              ...(lang ? { lang } : {}),
            }),
            EditorView.updateListener.of((update) => {
              if (update.docChanged) {
                onChangeRef.current(update.state.doc.toString());
              }
            }),
          ],
        }),
      });
      viewRef.current = view;
      const observer = new ResizeObserver(() => view.requestMeasure());
      observer.observe(parent);
      return () => {
        observer.disconnect();
        view.destroy();
        viewRef.current = null;
      };
    } catch {
      setFailed(true);
      return undefined;
    }
    // Mount once. External value sync is the second effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    return <TypstTextareaFallback value={value} onChange={onChange} ariaLabel={ariaLabel} lang={lang} />;
  }

  return <div ref={parentRef} className="typst-cm-host" />;
}

export default function TypstSourceEditor(props: TypstSourceEditorProps) {
  return <TypstCodeMirror {...props} />;
}
```

- [ ] **Step 3: Add host CSS**

In `apps/frontend/app/[locale]/globals.css` after `.typst-editor`:

```css
.typst-cm-host {
  height: 100%;
  min-height: 0;
}

.typst-cm-host .cm-editor {
  height: 100%;
}
```

Keep `.typst-editor` for the fallback textarea.

---

### Task 2: Wire EditorShell

**Files:**
- Modify: `apps/frontend/components/editor/EditorShell.tsx`

**Interfaces:**
- Consumes: `TypstSourceEditor` default export and `TypstSourceEditorProps`
- Produces: Typst tab renders CodeMirror; chat/import/`source` behavior unchanged

- [ ] **Step 1: Dynamic import and replace textarea**

Add:

```tsx
import dynamic from "next/dynamic";

const TypstSourceEditor = dynamic(
  () => import("@/components/editor/TypstSourceEditor"),
  { ssr: false },
);
```

Replace the textarea in the Typst `Tabs.Content` with:

```tsx
<TypstSourceEditor
  value={source}
  onChange={setSource}
  ariaLabel={t("tabTypst")}
  lang={locale}
/>
```

If the dynamic import throws, wrap with a class error boundary in `TypstSourceEditor.tsx` that renders `TypstTextareaFallback`. Add this to `TypstSourceEditor.tsx`:

```tsx
import { Component, type ReactNode } from "react";

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

export default function TypstSourceEditor(props: TypstSourceEditorProps) {
  return (
    <EditorErrorBoundary fallback={<TypstTextareaFallback {...props} />}>
      <TypstCodeMirror {...props} />
    </EditorErrorBoundary>
  );
}
```

Do not change chat PUT-before-send, 400ms SVG, 2s PDF, or import polling.

---

### Task 3: Lint and browser verification

**Files:**
- Touched frontend files only

- [ ] **Step 1: Lint**

```bash
cd apps/frontend && npm run lint
```

Expected: exit 0. Fix every lint error on touched files.

- [ ] **Step 2: Browser**

With the app at `http://127.0.0.1:3100/`:

1. Create a resume → editor Typst tab shows CodeMirror (gutters / line numbers), not a plain textarea.
2. Syntax is highlighted; indent and undo work.
3. Typing still refreshes the SVG preview after ~400ms.
4. Chat tab still shows composer; switching back to Typst keeps height (ResizeObserver).
5. If an import/chat replace updates `source`, the document text matches without a stuck cursor on no-op updates.

---

## Self-review

1. Spec coverage: CodeMirror + `typst_lezer` (Task 1), EditorShell `source` ownership (Task 2), fallback (Tasks 1–2), CSS host (Task 1), lint + browser (Task 3). Out of scope left out.
2. Placeholders: none.
3. Types: `TypstSourceEditorProps` is consistent across tasks.
