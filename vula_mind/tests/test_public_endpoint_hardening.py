"""Public endpoints: signed unsubscribe links and menu escaping (2026-09-25 review)."""
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.api import email_public, menu_page


def _unsub_app():
    app = FastAPI()
    app.include_router(email_public.router)
    return TestClient(app)


def test_signed_unsubscribe_link_is_one_click_and_unsigned_needs_a_click():
    db = MagicMock()
    with patch.object(email_public, "_client", return_value=db):
        url = email_public.unsubscribe_url("", "t1", "Jo@Example.com")
        r = _unsub_app().get(url)
        assert r.status_code == 200 and "unsubscribed" in r.text
        assert db.table.return_value.upsert.call_count == 1

        r = _unsub_app().get("/email/unsubscribe?tenant=t1&email=victim@example.com")
        assert "<form" in r.text and db.table.return_value.upsert.call_count == 1

        r = _unsub_app().post("/email/unsubscribe", data={"tenant": "t1", "email": "victim@example.com"})
        assert "unsubscribed" in r.text and db.table.return_value.upsert.call_count == 2


def test_unsubscribe_page_escapes_the_address():
    with patch.object(email_public, "_client", return_value=MagicMock()):
        r = _unsub_app().get("/email/unsubscribe?tenant=t1&email=<script>x</script>@e.com")
    assert "<script>" not in r.text


def test_menu_escape_covers_attribute_quotes():
    out = menu_page._esc('x" onerror="alert(1)')
    assert '"' not in out and "&quot;" in out


def test_broadcast_without_template_is_refused():
    """With the Twilio free-text fallback gone, a broadcast with no template used to 'send' an
    empty template name to every recipient and fail them all."""
    import asyncio
    from fastapi import HTTPException
    from vula.api.commerce import admin_send_broadcast
    from unittest.mock import MagicMock, patch
    try:
        with patch("vula.commerce.service._client", return_value=MagicMock()):
            asyncio.run(admin_send_broadcast("t1", {"body": "hi all", "dry_run": False,
                                                    "test_phone": "27820000000"}))
    except HTTPException as exc:
        assert exc.status_code == 400 and "template" in exc.detail
    else:
        raise AssertionError("expected 400")
