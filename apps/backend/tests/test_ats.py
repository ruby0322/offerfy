import json
import shutil
from pathlib import Path

import pytest

from app.services.ats import ATS_CHECK_NAMES, analyze_pdf
from app.services.typst_compile import compile_typst, typst_available

FIXTURES = Path(__file__).resolve().parents[1] / "docs" / "scoring" / "fixtures"
MANIFEST = FIXTURES / "manifest.json"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_analyze_pdf_returns_named_pass_fail_checks_without_score():
    # Minimal empty-ish PDF bytes should still return the 8 checks.
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    report = analyze_pdf(pdf, typst_source="")
    assert "score" not in report
    assert "grade" not in report
    names = [c["name"] for c in report["checks"]]
    assert names == list(ATS_CHECK_NAMES)
    for check in report["checks"]:
        assert set(check) == {"name", "passed"}
        assert isinstance(check["passed"], bool)


def test_manifest_has_at_least_30_fixtures_across_locales():
    data = _manifest()
    fixtures = data["fixtures"]
    assert len(fixtures) >= 30
    locales = {row["locale"] for row in fixtures}
    assert {"en", "zh-TW", "zh-CN"} <= locales
    for row in fixtures:
        path = FIXTURES / row["file"]
        assert path.is_file(), row["file"]
        expected = row["expected"]
        for name in ATS_CHECK_NAMES:
            assert name in expected, f"{row['file']} missing {name}"


def _check_map(report: dict) -> dict[str, bool]:
    return {c["name"]: c["passed"] for c in report["checks"]}


@pytest.mark.parametrize(
    "check_name",
    list(ATS_CHECK_NAMES),
)
def test_each_check_has_pass_and_fail_fixtures(check_name: str):
    data = _manifest()
    passing = [
        row["file"]
        for row in data["fixtures"]
        if row["expected"][check_name] is True
    ]
    failing = [
        row["file"]
        for row in data["fixtures"]
        if row["expected"][check_name] is False
    ]
    assert passing, f"no passing fixture for {check_name}"
    if check_name == "fonts_embedded":
        # Typst embeds fonts; fail path is covered by a synthetic PDF below.
        return
    assert failing, f"no failing fixture for {check_name}"


def test_fonts_embedded_fails_when_basefont_has_no_file():
    # Helvetica Type1 with no FontDescriptor / FontFile.
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 44>>stream\n"
        b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    report = analyze_pdf(pdf, typst_source="")
    got = {c["name"]: c["passed"] for c in report["checks"]}
    assert got["fonts_embedded"] is False


@pytest.mark.skipif(
    not typst_available() and not shutil.which("typst"),
    reason="typst binary missing",
)
@pytest.mark.parametrize(
    "fixture_row",
    _manifest()["fixtures"] if MANIFEST.is_file() else [],
    ids=lambda row: row["file"] if isinstance(row, dict) else str(row),
)
def test_compiled_fixture_matches_manifest(fixture_row: dict):
    source = (FIXTURES / fixture_row["file"]).read_text(encoding="utf-8")
    pdf = compile_typst(source, "pdf")
    report = analyze_pdf(pdf, source)
    got = _check_map(report)
    for name, expected in fixture_row["expected"].items():
        assert got[name] is expected, (
            f"{fixture_row['file']} {name}: expected {expected} got {got[name]}"
        )
    if fixture_row.get("name"):
        # Good fixtures recover the author string from the PDF text.
        from app.services.ats import extract_pdf_text

        text = extract_pdf_text(pdf)
        assert fixture_row["name"] in text or all(
            part in text for part in fixture_row["name"].split()
        )
    if fixture_row.get("email"):
        from app.services.ats import extract_pdf_text

        text = extract_pdf_text(pdf)
        assert fixture_row["email"] in text
