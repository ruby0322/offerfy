import pytest

from app.services.templates import (
    apply_prompt,
    find_thumbnail,
    is_template_switch_message,
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
    assert is_template_switch_message(text) is True
    assert is_template_switch_message("Rename me to Ada Lovelace") is False
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
    assert "read_typst" in row["apply_prompt"]
    assert "apply_typst_edit" in row["apply_prompt"]


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
