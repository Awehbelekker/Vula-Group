"""The inbound ClickUp webhook must authenticate, and must not drop what the team cares about.

Found 2026-09-01: POST /v1/clickup/webhook accepted ANY unauthenticated request, yet it mutates
real state — it updates field-ops task status and can trigger procurement posting. Anyone who
knew the URL could mark work complete. Same class of hole as the unauthenticated Yoco webhook.

ClickUp signs every delivery with a per-webhook secret (X-Signature = HMAC-SHA256 of the raw
body, hex — verified against developer.clickup.com/docs/webhooksignature). That secret was being
thrown away at registration, so there was nothing to verify against.

Separately, taskUpdated was already subscribed but the handler returned early on anything that
wasn't a status change, so assignments, due-date moves, priority changes and comments never
reached anyone.
"""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

import vula.api.clickup as cu


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _Req:
    def __init__(self, payload: dict, signature: str = ""):
        self._raw = json.dumps(payload).encode()
        self.headers = {"X-Signature": signature} if signature else {}

    async def body(self):
        return self._raw

    async def json(self):
        return json.loads(self._raw)


SECRET = "wh-secret-abc"
PAYLOAD = {"event": "taskStatusUpdated", "task_id": "abc123",
           "history_items": [{"field": "status", "after": {"status": "complete"}}]}


# ── signature verification ──────────────────────────────────────────────────────

def test_signature_helper_matches_clickups_scheme():
    raw = b'{"event":"taskCreated","task_id":"c0j"}'
    assert cu._verify_signature(raw, _sign(raw, SECRET), SECRET) is True
    assert cu._verify_signature(raw, "deadbeef", SECRET) is False
    assert cu._verify_signature(raw, "", SECRET) is False
    assert cu._verify_signature(raw, _sign(raw, SECRET), "") is False


@pytest.mark.asyncio
async def test_unsigned_request_is_rejected():
    """The exact hole: an anonymous POST must not be able to change task state."""
    req = _Req(PAYLOAD)
    with patch.object(cu, "_any_stored_webhook_secret", lambda: [("digg-demo", SECRET)]), \
         patch("vula.integrations.clickup_sync.field_task_for_clickup") as link:
        out = await cu.webhook(req)
    assert out["status"] == "unverified"
    link.assert_not_called(), "no state may be touched on an unverified request"


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected():
    raw = json.dumps(PAYLOAD).encode()
    req = _Req(PAYLOAD, signature=_sign(raw, "somebody-elses-secret"))
    with patch.object(cu, "_any_stored_webhook_secret", lambda: [("digg-demo", SECRET)]):
        out = await cu.webhook(req)
    assert out["status"] == "unverified"


@pytest.mark.asyncio
async def test_no_stored_secret_fails_closed():
    """Pre-migration-151 must refuse, not fall back to trusting the caller."""
    raw = json.dumps(PAYLOAD).encode()
    req = _Req(PAYLOAD, signature=_sign(raw, SECRET))
    with patch.object(cu, "_any_stored_webhook_secret", lambda: []):
        out = await cu.webhook(req)
    assert out["status"] == "unverified"
    assert out["reason"] == "no_secret_stored"


@pytest.mark.asyncio
async def test_correctly_signed_request_is_processed():
    raw = json.dumps(PAYLOAD).encode()
    req = _Req(PAYLOAD, signature=_sign(raw, SECRET))
    with patch.object(cu, "_any_stored_webhook_secret", lambda: [("digg-demo", SECRET)]), \
         patch("vula.integrations.clickup_sync.field_task_for_clickup",
               return_value={"field_task_id": "ft1"}), \
         patch("vula.models.field_ops.get_field_ops_db") as db:
        out = await cu.webhook(req)
    assert out["status"] == "ok"
    assert out["new_status"] == "complete"
    db.return_value.update_task_status.assert_called_once_with("ft1", "complete")


@pytest.mark.asyncio
async def test_the_signature_identifies_which_tenant_sent_it():
    """ClickUp's payload carries no tenant id — the validating secret IS the identification."""
    raw = json.dumps(PAYLOAD).encode()
    req = _Req(PAYLOAD, signature=_sign(raw, "digg-secret"))
    with patch.object(cu, "_any_stored_webhook_secret",
                      lambda: [("off-the-hook", "oth-secret"), ("digg-demo", "digg-secret")]), \
         patch("vula.integrations.clickup_sync.field_task_for_clickup", return_value=None), \
         patch("vula.clickup.service.get_task", AsyncMock(return_value={"id": "abc123"})), \
         patch("vula.integrations.procurement.handle_task_status_change",
               AsyncMock(return_value=False)) as proc:
        out = await cu.webhook(req)
    assert out["tenant_id"] == "digg-demo", "must attribute to the signing tenant"
    assert proc.await_args[0][0] == "digg-demo"


# ── events that used to be silently dropped ─────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("event,expect", [
    ("taskAssigneeUpdated", "assigned to"),
    ("taskDueDateUpdated", "Due date changed"),
    ("taskPriorityUpdated", "priority"),
    ("taskCreated", "New ClickUp task"),
])
async def test_non_status_events_notify_the_team(event, expect):
    body = {"event": event, "task_id": "t1", "history_items": [
        {"field": "assignee_add", "after": {"username": "Judy"}},
        {"field": "priority", "after": {"priority": "urgent"}},
    ]}
    with patch("vula.clickup.service.get_task",
               AsyncMock(return_value={"name": "Site inspection", "url": "http://cu/t1"})), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        out = await cu._handle_non_status_event("digg-demo", event, "t1", body)
    assert out["status"] == "ok" and out["notified"] is True
    assert expect.lower() in notify.await_args[0][2].lower()


@pytest.mark.asyncio
async def test_a_comment_reaches_the_team_with_its_actual_words():
    body = {"event": "taskCommentPosted", "task_id": "t1", "history_items": [
        {"user": {"username": "Judy"},
         "comment": {"text_content": "Client moved it to Thursday"}}]}
    with patch("vula.clickup.service.get_task",
               AsyncMock(return_value={"name": "Site inspection", "url": ""})), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        out = await cu._handle_non_status_event("digg-demo", "taskCommentPosted", "t1", body)
    msg = notify.await_args[0][2]
    assert out["status"] == "ok"
    assert "Client moved it to Thursday" in msg
    assert "Judy" in msg


@pytest.mark.asyncio
async def test_comment_text_is_read_back_when_absent_from_the_payload():
    body = {"event": "taskCommentPosted", "task_id": "t1", "history_items": []}
    with patch("vula.clickup.service.get_task",
               AsyncMock(return_value={"name": "Site inspection", "url": ""})), \
         patch("vula.clickup.service.list_comments",
               AsyncMock(return_value=[{"text": "Fetched from the API", "by": "Nolo"}])), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await cu._handle_non_status_event("digg-demo", "taskCommentPosted", "t1", body)
    assert "Fetched from the API" in notify.await_args[0][2]


@pytest.mark.asyncio
async def test_an_unknown_event_is_ignored_quietly():
    with patch("vula.clickup.service.get_task", AsyncMock(return_value={"name": "X"})):
        out = await cu._handle_non_status_event("digg-demo", "goalCreated", "t1", {})
    assert out["status"] == "ignored_event"


def test_registered_events_cover_the_ones_the_team_cares_about():
    from vula.clickup.service import WEBHOOK_EVENTS
    for e in ("taskStatusUpdated", "taskAssigneeUpdated", "taskDueDateUpdated",
              "taskPriorityUpdated", "taskCommentPosted", "taskCreated"):
        assert e in WEBHOOK_EVENTS
