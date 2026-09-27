"""Externally-named files can't escape the tenant's upload folder (2026-09-25 review)."""
from pathlib import Path

from vula.uploads import safe_filename, safe_upload_path


def test_traversal_and_absolute_names_are_flattened(tmp_path):
    d = tmp_path / "t1"
    d.mkdir()
    for evil in ("../../other/x.pdf", "/etc/passwd", "..\\..\\x.pdf", "..", ".", "", "a/../../b.pdf"):
        p = safe_upload_path(d, evil)
        assert p.resolve().parent == d.resolve(), evil


def test_readable_names_kept():
    assert safe_filename("Invoice March (2).pdf") == "Invoice March (2).pdf"
    assert safe_filename(".hidden") == "hidden"
    assert safe_filename("a\x00b.pdf") == "ab.pdf"
    long = "x" * 400 + ".pdf"
    out = safe_filename(long)
    assert len(out) <= 180 and out.endswith(".pdf")
