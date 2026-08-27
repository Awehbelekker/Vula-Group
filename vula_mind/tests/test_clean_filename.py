"""Tests for whatsapp.py's _clean_filename — a WhatsApp-delivered filename can arrive already
containing a literal U+FFFD replacement character (confirmed live 2026-08-27, a real Gerflor
"catálogo.pdf" upload lost its accented byte before Vula's webhook handler ever saw it). Since
U+FFFD discards the original byte, repair is impossible — the fix is graceful degradation to a
clean generic name, never propagating garbled Unicode into storage/display."""
import pytest

from vula.api.whatsapp import _clean_filename


def test_clean_filename_passes_through_a_normal_name():
    assert _clean_filename("Mipolam Classic - Catalogo.pdf") == "Mipolam Classic - Catalogo.pdf"


def test_clean_filename_replaces_mangled_name_keeps_extension():
    assert _clean_filename("Mipolam Classic - Cat�logo.pdf") == "document.pdf"


def test_clean_filename_replaces_mangled_name_no_extension():
    assert _clean_filename("Cat�logo") == "document"


def test_clean_filename_strips_control_characters():
    assert _clean_filename("report\x00.pdf") == "document.pdf"


def test_clean_filename_allows_tab_character():
    # Tabs aren't the kind of corruption this guards against — don't over-trigger on them.
    assert _clean_filename("report\tfinal.pdf") == "report\tfinal.pdf"


@pytest.mark.parametrize("filename", ["", None])
def test_clean_filename_passes_through_empty_or_none(filename):
    assert _clean_filename(filename) == filename


def test_clean_filename_replacement_char_mid_extension():
    # Even a corrupted extension degrades cleanly rather than producing a weird partial name.
    assert _clean_filename("report.p�f") == "document.p�f"
