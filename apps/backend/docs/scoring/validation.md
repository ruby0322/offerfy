# ATS parseability validation pack

Phase 1 analysis is **parseability of the compiled PDF**, not hireability.
Each fixture is a `.typ` file. Pytest compiles with Typst 0.15.1 (skipped if
the `typst` binary is missing) and runs `analyze_pdf` against `manifest.json`.

## Checks (pass/fail only)

| name | pass | fail (traps) |
| --- | --- | --- |
| `text_extractable` | extracted text is non-empty | image-only / no text |
| `single_column` | no two substantial text boxes overlap in y with a large x gap | two-column grid |
| `contact_in_body` | name and email in the page body (not header/footer-only) | header-only or missing email |
| `standard_headings` | Education/Experience or 學歷/工作經歷/教育/工作经历/教育经历 as `==` headings | no those headings |
| `dates_machine_readable` | **every** range resolves to a month: `YYYY-MM` or `Mon YYYY`, ending Present/至今/现在 | year-only ranges, `Summer 2020`, `'21`, no dates |
| `no_embedded_images_as_text` | photos/icons are not the only contact/name | raster image and name/email missing from text |
| `fonts_embedded` | fonts used in the PDF have embedded font files | (Typst embeds; unit-tested with a synthetic PDF) |
| `parse_roundtrip_ok` | if `#let name = "..."` is set, extract recovers it | name in source but not drawn |

There is **no weighted score and no A–F grade**.

## Header/footer band

The spec approximates header/footer as the top/bottom ~8% of the page.
`basic-resume` places the author name just after a 0.5in margin (~4.3% from
the top of A4). The checker therefore uses a **top 4%** header strip (page
`header` in the margin) and a **bottom 8%** footer strip so in-body contact
still passes and header/footer-only contact fails.

## Layout

- `fixtures/*.typ` — ≥30 golden files across `en`, `zh-TW`, `zh-CN`
- `fixtures/manifest.json` — expected pass/fail per check, plus `name`/`email` when set

Good fixtures set name + email, Education/Experience (or locale headings), and
`YYYY-MM` / Present dates so `parse_roundtrip_ok`, `contact_in_body`,
`standard_headings`, and `dates_machine_readable` pass after compile.

## Why the date check is all-of

`dates_machine_readable` is the one check that is not satisfiable by a single
match. A vendor extractor that cannot resolve a range to a month leaves its
Dates field blank, and a blank required field can keep the application out of
recruiter search entirely — so a resume whose ranges are year-only
(`2018 — 2022`) fails even when a current role says `Present`
(`trap_year_only_dates.typ`). `Mon YYYY` is accepted alongside `YYYY-MM`
because it is the form ATS vendors' own guidance prescribes
(`en_good_month_names.typ`); a lone year in prose ("ICPC 2022") is not read as
a date entry at all.
