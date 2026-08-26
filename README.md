# Offerfy

AI-native job engine. Phase 1 sells the agentic loop (Search → Enhance → Tailor → Apply) and ships master-resume editing with ATS parseability.

- `/` is original Offerfy (navy/teal). Create and upload skip the picker and open the Typst editor.
- Upload, auth, dashboard, and service selection use a RenderResume-like visual shell (style only).
- Editor: Typst source | AI chat (one tool: `apply_typst_edit`) and a live preview with an ATS pass/fail strip.
- Product copy never uses CareerOS, Roleloop, Offerly, Offerloop, or RenderResume.

See [docs/superpowers/specs/2026-08-26-offerfy-design.md](docs/superpowers/specs/2026-08-26-offerfy-design.md).

## Local

```bash
cp .env.example .env
docker compose up --build
```

Frontend: http://localhost:3000 (default locale `zh-TW`). API is rewritten from `/api/*` to the FastAPI backend.

Backend tests:

```bash
cd apps/backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q
```

Typst CLI 0.15.1 is required for golden ATS compile tests (`apps/backend/.tools/typst` or `typst` on `PATH`).
