"""Meta Graph API version in one place (31 Oct 2026).

v19.0 was hard-coded ~25 times across the backend and the dashboard's FB SDK init, and left Meta's
support window on 21 May 2026. The version now comes from settings.graph_api_version
(GRAPH_API_VERSION) and every Graph URL is built by vula/meta_graph.py::graph_url; the dashboard
reads VITE_FB_GRAPH_VERSION. This guard fails on any versioned Graph literal left anywhere else.
"""
import re
from pathlib import Path

from config import settings
from vula import meta_graph

ROOT = Path(__file__).resolve().parents[1]          # vula_mind/
DASH_SRC = ROOT.parent / "vula_dashboard" / "src"

# A versioned Graph URL, or an FB SDK `version: 'v19.0'` literal.
_PY_PATTERN = re.compile(r"graph\.facebook\.com/v\d+")
_JS_PATTERN = re.compile(r"graph\.facebook\.com/v\d+|version:\s*['\"`]v\d+")


def _hits(paths, pattern):
    out = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                out.append(f"{path}:{n}: {line.strip()}")
    return out


def test_no_hard_coded_graph_version_in_backend():
    files = [ROOT / "config.py"]
    for sub in ("vula", "core"):
        files += [p for p in (ROOT / sub).rglob("*.py") if "__pycache__" not in p.parts]
    assert _hits(files, _PY_PATTERN) == [], "use vula.meta_graph.graph_url() instead"


def test_no_hard_coded_graph_version_in_dashboard():
    if not DASH_SRC.exists():
        return
    files = [p for p in DASH_SRC.rglob("*") if p.suffix in (".js", ".jsx", ".ts", ".tsx")]
    assert _hits(files, _JS_PATTERN) == [], "use VITE_FB_GRAPH_VERSION instead"


def test_default_version_is_supported():
    assert type(settings).model_fields["graph_api_version"].default == "v25.0"


def test_graph_url_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "graph_api_version", "v26.0")
    assert meta_graph.graph_url("123/messages") == "https://graph.facebook.com/v26.0/123/messages"
    assert meta_graph.graph_url("/123") == "https://graph.facebook.com/v26.0/123"
    assert meta_graph.graph_base() == "https://graph.facebook.com/v26.0"
    assert settings.whatsapp_api_url == "https://graph.facebook.com/v26.0"   # the legacy name


def test_a_bare_or_blank_version_still_builds_a_valid_url(monkeypatch):
    monkeypatch.setattr(settings, "graph_api_version", "25.0")
    assert meta_graph.graph_base() == "https://graph.facebook.com/v25.0"
    monkeypatch.setattr(settings, "graph_api_version", "  ")
    assert meta_graph.graph_base() == "https://graph.facebook.com/v25.0"
