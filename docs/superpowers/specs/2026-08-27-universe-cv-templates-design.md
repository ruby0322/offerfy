# Template tab (Universe CV)

Editor left panel adds a **Template** tab beside Typst and Chat.

- Catalog: `GET https://packages.typst.org/preview/index.json` (official, free). Filter `categories` contains `cv`. Keep the latest version per package name. No HTML scrape.
- Cache: unpack `https://packages.typst.org/preview/{name}-{version}.tar.gz` into `{TYPST_PACKAGE_PATH}/preview/{name}/{version}/`. Prefetch in a background thread after process start; do not block `/readyz`. Skip archives that already exist. Each prod replica prefetches its own disk (writable container FS).
- `GET /v1/templates` returns `{ templates: [{ name, version, description, universe_url, import_line, apply_prompt, cached }] }`.
- **套用** does not rewrite Typst in the API. The client switches to Chat and sends `apply_prompt` as a normal user chat message so the model can `read_typst` then full-source `apply_typst_edit`.
