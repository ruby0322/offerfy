from app.services.template_probe import inline_data_files


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
