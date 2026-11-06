from pathlib import Path

from app.services.template_probe import (
    font_warnings,
    inline_data_files,
    probe_template,
    unavailable_fonts,
)


def test_inline_data_files_replaces_the_loader_call(tmp_path):
    (tmp_path / "cv.yaml").write_text("name: Ruby\n", encoding="utf-8")
    src = '#let data = yaml("cv.yaml")\n#data.name'
    out, inlined = inline_data_files(src, tmp_path)
    assert 'yaml("cv.yaml")' not in out
    assert '"name": "Ruby"' in out
    assert inlined == ("cv.yaml",)


def test_inline_data_files_handles_toml_and_json(tmp_path):
    (tmp_path / "cv.toml").write_text('name = "Ruby"\n', encoding="utf-8")
    (tmp_path / "cv.json").write_text('{"name": "Ruby"}', encoding="utf-8")

    toml_src = '#let data = toml("cv.toml")\n#data.name'
    toml_out, toml_inlined = inline_data_files(toml_src, tmp_path)
    assert 'toml("cv.toml")' not in toml_out
    assert '"name": "Ruby"' in toml_out
    assert toml_inlined == ("cv.toml",)

    json_src = '#let data = json("cv.json")\n#data.name'
    json_out, json_inlined = inline_data_files(json_src, tmp_path)
    assert 'json("cv.json")' not in json_out
    assert '"name": "Ruby"' in json_out
    assert json_inlined == ("cv.json",)


def test_inline_data_files_leaves_source_alone_when_the_file_is_absent(tmp_path):
    src = '#let data = yaml("missing.yaml")\n#data.name'
    out, inlined = inline_data_files(src, tmp_path)
    assert out == src
    assert inlined == ()


def test_inline_data_files_ignores_a_computed_path(tmp_path):
    # sys.inputs-driven loaders (polycv, porygon) are out of reach by design
    src = '#let p = sys.inputs.at("CV", default: "cv.json")\n#let d = json(p)'
    assert inline_data_files(src, tmp_path) == (src, ())


def test_font_warnings_reads_every_unknown_family():
    err = (
        "warning: unknown font family: calibri\n"
        "warning: unknown font family: font awesome 7 free\n"
    )
    assert font_warnings(err) == ("calibri", "font awesome 7 free")


def test_unavailable_fonts_separates_what_we_could_ship():
    seen = ("calibri", "font awesome 7 free", "pt serif")
    assert unavailable_fonts(seen) == ("calibri",)


def test_probe_rejects_a_name_that_is_not_a_package_name():
    row = probe_template("../etc", "1.0.0", root=Path("/nonexistent"))
    assert row.ok is False and row.reason == "invalid name"


def test_probe_reports_no_example_when_the_package_ships_none(tmp_path):
    dest = tmp_path / "preview" / "no-example-pkg" / "1.0.0"
    dest.mkdir(parents=True)
    (dest / "typst.toml").write_text(
        '[package]\nname = "no-example-pkg"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    row = probe_template("no-example-pkg", "1.0.0", root=tmp_path)
    assert row.ok is False and row.reason == "no example"
