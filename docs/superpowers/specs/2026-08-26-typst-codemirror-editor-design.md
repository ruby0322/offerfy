# Typst CodeMirror editor

> Amends [2026-08-26-offerfy-design.md](2026-08-26-offerfy-design.md) Editor: the Typst tab is no longer a plain `<textarea>`. Preview, chat tools, ATS, and compile stay server-side.

## Goal

Replace the Typst-tab textarea with CodeMirror 6 so editing `.typ` source feels like a real editor: syntax highlighting, line numbers, undo/redo, indent, search, bracket matching. Chat, import fill, autosave, and live preview keep using the same React `source` string. Do not compile Typst in the browser.

## Architecture

- New client component `TypstSourceEditor` mounts CodeMirror in the Typst tab.
- Language: `typst_lezer()` from `codemirror-lang-typst/lezer` (Typst 0.15, node names from official `typst-syntax`, no WASM). Next 16 / Turbopack stays simple.
- `EditorShell` remains the owner of `source`. Typing, chat `apply_typst_edit`, and import completion all write that string. Existing 400ms SVG debounce, 2s PDF compile, PUT-before-chat, and ATS refresh are unchanged.
- CodeMirror loads only on the client (`dynamic(..., { ssr: false })`). If the module fails to load, fall back to the current textarea so the tab still works.

## Components

| Unit | Does | Depends on |
| --- | --- | --- |
| `TypstSourceEditor` | Hosts CodeMirror; reports `onChange`; applies external `value` when it differs from the document | `codemirror`, `@codemirror/*`, `codemirror-lang-typst/lezer` |
| `EditorShell` | Tabs, chat, preview, save/compile timers; passes `source` / `setSource` | Unchanged API (`putResumeSource`, chat, compile) |

Extensions (minimum): `typst_lezer()`, CodeMirror `autocompletion()` for Typst 0.15 builtins, line numbers, history, indent-on-input, search, bracket matching, light syntax highlight theme matching `.typst-editor` (`#f8fafc` background, RR chrome).

Layout, Chat tab, preview pane, and ATS strip do not change.

## Data flow

1. User types → CodeMirror updates → `onChange` → `setSource` → existing save/preview effects.
2. Chat or import returns new Typst → `setSource` → editor `dispatch` a full-document replace **only if** current document text ≠ incoming value (do not reset the cursor when they already match).
3. Chat send still `PUT`s current `source` before `POST` chat.

## Error handling

- CodeMirror import/init failure → textarea fallback; no blank Typst tab.
- Compile/preview/ATS errors stay as today (`previewError` / `compileError`).
- Do not add a second in-browser diagnostic channel that disagrees with server compile.

## Testing / verification

- `apps/frontend`: `npm run lint` clean on touched files.
- Browser: create and upload → editor. Confirm highlight, line numbers, indent, undo; typing still autosaves and refreshes SVG; chat apply and import fill replace the document; Chat tab still works.
- No new backend tests.

## Out of scope

In-browser Typst compile or SVG, Tinymist LSP, Monaco, collab, dark theme, changing the split layout, RR smart-chat, extra editor chrome.

## Files

- Add `apps/frontend/components/editor/TypstSourceEditor.tsx`
- Edit `apps/frontend/components/editor/EditorShell.tsx`, `apps/frontend/app/[locale]/globals.css`, `apps/frontend/package.json`
- This spec; one-line Editor bullet in the parent Offerfy spec
