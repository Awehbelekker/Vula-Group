"""
vula/meta_graph.py — every Meta Graph API URL is built here.

The version comes from settings.graph_api_version (GRAPH_API_VERSION, default in config.py).
Read at call time, so a test or an operator override applies without re-importing anything.
"""
from __future__ import annotations


def graph_base() -> str:
    """https://graph.facebook.com/<version>"""
    from config import settings
    return settings.graph_base


def graph_url(path: str = "") -> str:
    """graph_url("123/messages") → https://graph.facebook.com/<version>/123/messages"""
    path = (path or "").lstrip("/")
    return f"{graph_base()}/{path}" if path else graph_base()
