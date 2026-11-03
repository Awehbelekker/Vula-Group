"""tools/staging_flows.py: the stand-in Meta and the signed webhooks it sends to local staging."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import staging_flows as sf  # noqa: E402


def test_fake_meta_records_text_and_buttons():
    m = sf.FakeMeta.__new__(sf.FakeMeta)
    m.sent, m.media = [], {}
    import threading
    m.lock = threading.Lock()
    m._record({"to": "27820000001", "type": "text", "text": {"body": "hi"}})
    m._record({"to": "+27820000001", "type": "interactive", "interactive": {
        "body": {"text": "Price it?"},
        "action": {"buttons": [{"type": "reply", "reply": {"id": "cf:1:yes", "title": "Price it"}}]}}})
    got = m.to("27820000001")
    assert [s.text for s in got] == ["hi", "Price it?"]
    assert got[1].buttons == [{"id": "cf:1:yes", "title": "Price it"}]


def test_webhook_is_signed_like_meta(monkeypatch):
    seen = {}

    class R:
        status_code = 200

    def fake_post(url, content, headers, timeout):
        seen.update(url=url, content=content, headers=headers)
        return R()
    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    sf.post_webhook("http://127.0.0.1:7438", "s3cret", sf.text_msg("27830000001", "hello"))
    expected = "sha256=" + hmac.new(b"s3cret", seen["content"], hashlib.sha256).hexdigest()
    assert seen["headers"]["X-Hub-Signature-256"] == expected
    body = json.loads(seen["content"])
    value = body["entry"][0]["changes"][0]["value"]
    assert value["metadata"]["phone_number_id"] == sf.PNID
    assert value["messages"][0]["text"]["body"] == "hello"


def test_seed_refuses_anything_but_a_local_database(monkeypatch):
    remote = "postgresql://" + "postgres" + "@" + "db.example-project.supabase.co:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", remote)
    with pytest.raises(SystemExit):
        sf._local_dsn()
    monkeypatch.setenv("DATABASE_URL", "postgresql://" + "postgres" + "@127.0.0.1:54322/postgres")
    assert sf._local_dsn().startswith("postgresql://")
