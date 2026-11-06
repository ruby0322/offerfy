import json

import pytest

from app.services import templates
from app.services.template_probe import SupportRow
from app.services.templates import (
    apply_prompt,
    find_thumbnail,
    latest_cv_packages,
    package_cached,
    parse_preview_spec,
    reset_template_index_cache,
    resolve_thumbnail,
    template_example_source,
    thumbnail_relpath,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _reset_templates_cache():
    reset_template_index_cache()
    yield
    reset_template_index_cache()


def test_latest_cv_packages_picks_newest_and_skips_other_categories():
    rows = [
        {
            "name": "basic-resume",
            "version": "0.2.8",
            "categories": ["cv"],
            "description": "old",
        },
        {
            "name": "basic-resume",
            "version": "0.2.9",
            "categories": ["cv"],
            "description": "new",
        },
        {
            "name": "cetz",
            "version": "0.3.0",
            "categories": ["visualization"],
            "description": "plots",
        },
    ]
    latest = latest_cv_packages(rows)
    assert [row["name"] for row in latest] == ["basic-resume"]
    assert latest[0]["version"] == "0.2.9"
    assert latest[0]["description"] == "new"


def test_apply_prompt_asks_for_full_source_rewrite():
    text = apply_prompt("brilliant-cv", "2.0.0")
    assert '@preview/brilliant-cv:2.0.0' in text
    assert "read_typst" in text
    assert "apply_typst_edit" in text
    assert "search+replace" in text
    assert parse_preview_spec(text) == ("brilliant-cv", "2.0.0")
    assert parse_preview_spec("hello") is None


def test_template_example_source_reads_toml_entrypoint(tmp_path):
    dest = tmp_path / "preview" / "acorn-resume" / "0.1.0"
    (dest / "template").mkdir(parents=True)
    (dest / "typst.toml").write_text(
        '[package]\nname = "acorn-resume"\nversion = "0.1.0"\n\n'
        '[template]\npath = "template"\nentrypoint = "main.typ"\n'
    )
    (dest / "template" / "main.typ").write_text(
        '#import "@preview/acorn-resume:0.1.0": *\n#exp(role: "Intern")\n'
    )
    text = template_example_source("acorn-resume", "0.1.0", root=tmp_path)
    assert text is not None
    assert "#exp(" in text
    assert template_example_source("missing", "1.0.0", root=tmp_path) is None


def test_list_templates_endpoint(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.templates.fetch_index",
        lambda: [
            {
                "name": "basic-resume",
                "version": "0.2.9",
                "categories": ["cv"],
                "description": "ATS resume",
            }
        ],
    )
    monkeypatch.setattr("app.services.templates.package_root", lambda: tmp_path)
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps({"rows": [{"name": "basic-resume", "version": "0.2.9", "ok": True}]})
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    response = client.get("/v1/templates")
    assert response.status_code == 200
    body = response.json()
    assert len(body["templates"]) == 1
    row = body["templates"][0]
    assert row["name"] == "basic-resume"
    assert row["version"] == "0.2.9"
    assert row["cached"] is False
    assert row["universe_url"] == "https://typst.app/universe/package/basic-resume/"
    assert '@preview/basic-resume:0.2.9' in row["import_line"]
    assert "ATS resume" in row["description"]
    assert "apply_prompt" not in row
    # basic-resume:0.2.9 now has a real emitter (Task 8) -- the field is no
    # longer the hard-coded placeholder this test used to pin.
    assert row["deterministic"] is True


def test_thumbnail_relpath_reads_template_section():
    assert (
        thumbnail_relpath({"template": {"thumbnail": "assets/thumbnail_1.png"}})
        == "assets/thumbnail_1.png"
    )
    assert thumbnail_relpath({"thumbnail": "thumbnail.png"}) == "thumbnail.png"
    assert thumbnail_relpath({}) is None


def test_resolve_thumbnail_rejects_traversal_and_falls_back(tmp_path):
    dest = tmp_path / "pkg"
    dest.mkdir()
    (dest / "secret.png").write_bytes(PNG)
    (tmp_path / "passwd.png").write_bytes(PNG)
    assert resolve_thumbnail(dest, "../passwd.png") is None
    assets = dest / "assets"
    assets.mkdir()
    (assets / "thumbnail_2.png").write_bytes(PNG)
    found = resolve_thumbnail(dest, "assets/thumbnail_1.png")
    assert found is not None
    assert found.name == "thumbnail_2.png"


def test_preview_endpoint(client, monkeypatch, tmp_path):
    dest = tmp_path / "preview" / "basic-resume" / "0.2.9"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text("[package]\nname = \"basic-resume\"\n")
    (dest / "thumbnail.png").write_bytes(PNG)
    monkeypatch.setattr(
        "app.services.templates.fetch_index",
        lambda: [
            {
                "name": "basic-resume",
                "version": "0.2.9",
                "categories": ["cv"],
                "template": {"thumbnail": "thumbnail.png"},
            }
        ],
    )
    monkeypatch.setattr("app.services.templates.package_root", lambda: tmp_path)
    response = client.get("/v1/templates/basic-resume/preview")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    missing = client.get("/v1/templates/not-a-template/preview")
    assert missing.status_code == 404
    assert find_thumbnail("../etc/passwd", root=tmp_path) is None


class _FakeImageResponse:
    status_code = 200
    content = PNG
    headers = {"content-type": "image/png"}

    def raise_for_status(self) -> None:
        return None


class _FakeImageClient:
    last_url = ""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str):
        _FakeImageClient.last_url = url
        return _FakeImageResponse()


def test_preview_fetches_registry_when_tarball_omits_thumbnail(client, monkeypatch, tmp_path):
    dest = tmp_path / "preview" / "acadennial-cv" / "0.1.0"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text("[package]\nname = \"acadennial-cv\"\n")
    monkeypatch.setattr(
        "app.services.templates.fetch_index",
        lambda: [
            {
                "name": "acadennial-cv",
                "version": "0.1.0",
                "categories": ["cv"],
                "template": {"thumbnail": "examples/thumbnail-1.png"},
            }
        ],
    )
    monkeypatch.setattr("app.services.templates.package_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.templates.httpx.Client", _FakeImageClient)
    response = client.get("/v1/templates/acadennial-cv/preview")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "typst/packages" in _FakeImageClient.last_url
    assert "acadennial-cv/0.1.0/examples/thumbnail-1.png" in _FakeImageClient.last_url
    cached = tmp_path / ".universe-thumbs" / "acadennial-cv" / "0.1.0" / "thumbnail-1.png"
    assert cached.is_file()


def test_package_cached_true_when_typst_toml_present(tmp_path):
    dest = tmp_path / "preview" / "basic-resume" / "0.2.9"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text("[package]\nname = \"basic-resume\"\n")
    assert package_cached(tmp_path, "basic-resume", "0.2.9") is True
    assert package_cached(tmp_path, "missing", "1.0.0") is False


def test_supported_templates_returns_empty_when_the_file_is_missing(monkeypatch, tmp_path):
    # Review Focus 5: a missing list degrades to an empty picker, never a 500
    monkeypatch.setattr(templates, "SUPPORT_PATH", tmp_path / "absent.json")
    assert templates.supported_templates() == {}


def test_supported_templates_returns_empty_on_malformed_json(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(templates, "SUPPORT_PATH", bad)
    assert templates.supported_templates() == {}


def test_list_templates_drops_rows_that_are_not_supported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.templates.fetch_index",
        lambda: [
            {
                "name": "basic-resume",
                "version": "0.2.9",
                "categories": ["cv"],
                "description": "a resume",
            },
            {
                "name": "exp-cover-letter",
                "version": "1.0.0",
                "categories": ["cv"],
                "description": "a cover letter",
            },
        ],
    )
    monkeypatch.setattr("app.services.templates.package_root", lambda: tmp_path)
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps(
            {
                "rows": [
                    {"name": "basic-resume", "version": "0.2.9", "ok": True, "reason": "ok"},
                    {
                        "name": "exp-cover-letter",
                        "version": "1.0.0",
                        "ok": False,
                        "reason": "cover letter, not a resume",
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    rows = templates.list_templates()
    assert [row["name"] for row in rows] == ["basic-resume"]


def test_list_templates_offers_the_pinned_version_even_once_the_index_moves_on(
    monkeypatch, tmp_path
):
    # Decision 1: the index's "latest" for basic-resume has moved to 0.2.10,
    # but the starter/emitter are pinned to 0.2.9. Filtering the index's own
    # rows by is_supported would drop basic-resume outright (0.2.10 never
    # matches the support list's 0.2.9); list_templates() must instead offer
    # the pinned version the support list actually carries.
    monkeypatch.setattr(
        "app.services.templates.fetch_index",
        lambda: [
            {
                "name": "basic-resume",
                "version": "0.2.10",
                "categories": ["cv"],
                "description": "ATS resume",
            }
        ],
    )
    monkeypatch.setattr("app.services.templates.package_root", lambda: tmp_path)
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps({"rows": [{"name": "basic-resume", "version": "0.2.9", "ok": True}]})
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    rows = templates.list_templates()
    assert len(rows) == 1
    assert rows[0]["version"] == "0.2.9"
    assert rows[0]["description"] == "ATS resume"
    assert '@preview/basic-resume:0.2.9' in rows[0]["import_line"]


def test_is_supported_matches_on_name_and_version(monkeypatch, tmp_path):
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps({"rows": [{"name": "basic-resume", "version": "0.2.9", "ok": True}]})
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    assert templates.is_supported("basic-resume", "0.2.9") is True
    # a floated version is not the supported one
    assert templates.is_supported("basic-resume", "0.2.10") is False
    assert templates.is_supported("not-a-package", "1.0.0") is False


def test_denylist_entries_each_carry_a_reason():
    from app.jobs.survey_templates import DENYLIST

    assert set(DENYLIST) == {
        "exp-cover-letter",
        "modernpro-coverletter",
        "hadronic-rirekisho",
        "rirekisho-ofa",
        "shokumu-keirekisho-ofa",
        "scienceicons",
    }
    assert all(reason for reason in DENYLIST.values())


def _canned_probe(*, ok, pages=1, font_warnings=(), needs_inline_data=False):
    def probe(name, version, *, root):
        return SupportRow(
            name=name,
            version=version,
            ok=ok,
            pages=pages,
            needs_inline_data=needs_inline_data,
            font_warnings=font_warnings,
            installable_fonts=tuple(f for f in font_warnings if f == "pt serif"),
            reason="ok" if ok else "compile failed",
        )

    return probe


def test_survey_marks_a_denylisted_package_unsupported(monkeypatch, tmp_path):
    from app.jobs import survey_templates

    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))
    row = survey_templates.survey_row("exp-cover-letter", "1.0.0", {}, root=tmp_path)
    assert row["ok"] is False
    assert row["reason"] == survey_templates.DENYLIST["exp-cover-letter"]


def test_survey_marks_a_template_with_any_font_warning_unsupported(monkeypatch, tmp_path):
    from app.jobs import survey_templates

    # pt serif is on the installable list, but the survey still gates on
    # any warning at all -- probe_template's own docstring leaves deciding
    # what to do about a warning to this job, not to itself.
    monkeypatch.setattr(
        survey_templates, "probe_template", _canned_probe(ok=True, font_warnings=("pt serif",))
    )
    row = survey_templates.survey_row("some-cv", "1.0.0", {}, root=tmp_path)
    assert row["ok"] is False
    assert "pt serif" in row["reason"]


def test_survey_marks_a_template_over_two_pages_unsupported(monkeypatch, tmp_path):
    from app.jobs import survey_templates

    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True, pages=3))
    row = survey_templates.survey_row("some-cv", "1.0.0", {}, root=tmp_path)
    assert row["ok"] is False
    assert "3" in row["reason"]


def test_survey_pairs_prefer_the_emitter_pin_over_the_index_latest():
    from app.jobs import survey_templates

    index = [
        {
            "name": "basic-resume",
            "version": "0.2.10",
            "categories": ["cv"],
            "template": {"thumbnail": "thumbnail.png"},
        }
    ]
    pairs = survey_templates.survey_pairs(index)
    versions = {name: version for name, version, _row in pairs}
    assert versions["basic-resume"] == "0.2.9"


def test_survey_row_measures_font_warnings_on_the_emitter_output_when_one_exists(
    monkeypatch, tmp_path
):
    # Task 8's decision 2: once a pair has an emitter, gate 2 must measure
    # *its* output, not the vendor example's -- an emitter sets its font
    # explicitly, so a clean vendor-example probe (font_warnings=()) must
    # not hide a warning the emitter's own output actually produces.
    from app.jobs import survey_templates

    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))

    def fake_compile(source, fmt):
        assert 'font: "' in source  # the emitter's own explicit font argument
        return b"%PDF-fake", "warning: unknown font family: comic sans\n"

    monkeypatch.setattr(survey_templates, "compile_typst_with_stderr", fake_compile)
    row = survey_templates.survey_row("basic-resume", "0.2.9", {}, root=tmp_path)
    assert row["ok"] is False
    assert "comic sans" in row["reason"]
    assert row["font_warnings"] == ["comic sans"]


def test_survey_row_records_the_installable_and_unavailable_font_split(monkeypatch, tmp_path):
    # Review finding 1: font_warnings alone can't be split back into "we
    # could ship this by installing it" vs "nobody can bundle this" after
    # the fact -- both lists must be on the row, even though gate 2 still
    # rejects on any warning at all regardless of the split.
    from app.jobs import survey_templates
    from app.services.template_probe import INSTALLABLE_FONTS

    installable_font = next(iter(INSTALLABLE_FONTS))
    warnings = (installable_font, "calibri")

    def probe(name, version, *, root):
        return SupportRow(
            name=name,
            version=version,
            ok=True,
            pages=1,
            needs_inline_data=False,
            font_warnings=warnings,
            installable_fonts=(installable_font,),
            reason="ok",
        )

    monkeypatch.setattr(survey_templates, "probe_template", probe)
    row = survey_templates.survey_row("some-cv", "1.0.0", {}, root=tmp_path)
    assert row["ok"] is False
    assert row["installable_fonts"] == [installable_font]
    assert row["unavailable_fonts"] == ["calibri"]


def test_survey_row_passes_when_the_registry_serves_the_declared_thumbnail(monkeypatch, tmp_path):
    # Review finding 2: gate 4 (thumbnail) had no coverage at all.
    from app.jobs import survey_templates

    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))
    monkeypatch.setattr(
        survey_templates,
        "fetch_registry_thumbnail",
        lambda name, version, rel, *, root: tmp_path / "thumb.png",
    )
    row = survey_templates.survey_row(
        "some-cv", "1.0.0", {"template": {"thumbnail": "thumbnail.png"}}, root=tmp_path
    )
    assert row["ok"] is True
    assert row["reason"] == "ok"


def test_survey_marks_a_template_unsupported_when_the_registry_has_no_thumbnail(
    monkeypatch, tmp_path
):
    # Review finding 2: the fail path of gate 4 had no coverage either.
    from app.jobs import survey_templates

    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))
    monkeypatch.setattr(
        survey_templates,
        "fetch_registry_thumbnail",
        lambda name, version, rel, *, root: None,
    )
    row = survey_templates.survey_row(
        "some-cv", "1.0.0", {"template": {"thumbnail": "thumbnail.png"}}, root=tmp_path
    )
    assert row["ok"] is False
    assert row["reason"] == "no thumbnail"


def test_thumbnail_gate_falls_back_to_the_pinned_packages_own_typst_toml(monkeypatch, tmp_path):
    # Review finding 3: a pinned package that drops out of
    # latest_cv_packages() (stale/changed category tag, a rename) arrives
    # here with index_row == {}. The thumbnail gate must still find the
    # declared thumbnail from the package's own extracted typst.toml rather
    # than failing "no thumbnail" regardless of whether one exists.
    from app.jobs import survey_templates

    dest = tmp_path / "preview" / "acorn-resume" / "0.1.0"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text(
        '[package]\nname = "acorn-resume"\nversion = "0.1.0"\n\n'
        '[template]\npath = "template"\nentrypoint = "main.typ"\nthumbnail = "thumbnail.png"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))
    seen_rel = {}

    def fake_fetch(name, version, rel, *, root):
        seen_rel["rel"] = rel
        return dest / "thumbnail.png"

    monkeypatch.setattr(survey_templates, "fetch_registry_thumbnail", fake_fetch)
    row = survey_templates.survey_row("acorn-resume", "0.1.0", {}, root=tmp_path)
    assert row["ok"] is True
    assert seen_rel["rel"] == "thumbnail.png"


def test_thumbnail_gate_fails_when_nothing_declares_a_thumbnail(monkeypatch, tmp_path):
    # Neither the index row nor the package's own typst.toml declare a
    # thumbnail -- the fallback from finding 3 has nothing to fall back to.
    from app.jobs import survey_templates

    dest = tmp_path / "preview" / "no-thumb-cv" / "1.0.0"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text('[package]\nname = "no-thumb-cv"\n', encoding="utf-8")
    monkeypatch.setattr(survey_templates, "probe_template", _canned_probe(ok=True))
    row = survey_templates.survey_row("no-thumb-cv", "1.0.0", {}, root=tmp_path)
    assert row["ok"] is False
    assert row["reason"] == "no thumbnail"
