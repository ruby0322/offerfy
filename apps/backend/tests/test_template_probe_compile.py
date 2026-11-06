"""Probe real vendor examples through the real typst binary.

These two packages anchor the probe's two paths: `basic-resume` compiles as
shipped (no sidecar data, no font warnings), and `clean-print-cv` only
compiles after its vendor example's `yaml("cv-data.yaml")` call is inlined
(Task 1's `inline_data_files`). Gated on `typst_available()`, following
`tests/test_import_qa.py`'s pattern, since the pure logic in
`test_template_probe.py` needs no binary but these do.
"""

import pytest

from app.services.templates import package_root
from app.services.template_probe import probe_template
from app.services.typst_compile import typst_available

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")


def test_probe_accepts_basic_resume():
    row = probe_template("basic-resume", "0.2.9", root=package_root())
    assert row.ok is True and row.pages == 1 and row.font_warnings == ()


def test_probe_needs_inline_data_for_clean_print_cv():
    row = probe_template("clean-print-cv", "0.1.0", root=package_root())
    assert row.ok is True and row.needs_inline_data is True
