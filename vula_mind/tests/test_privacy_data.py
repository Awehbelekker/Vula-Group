"""DELETE removes every conversation record a number has, now, and says only what it did; the
export includes the commerce-path conversation it used to leave out (vula/privacy_data.py)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fake_supabase import FakeSupabase
from vula import kb_learning, privacy_data
from vula.commerce import service

T = "deli"
ME, OTHER = "27821112222", "27820000009"


def _db():
    asker = kb_learning.asker_key(T, ME)
    return FakeSupabase({
        "vula_chat_messages": [
            {"tenant_id": T, "phone": ME, "role": "user", "text": "a"},
            {"tenant_id": T, "phone": "0821112222", "role": "user", "text": "b"},        # other spelling
            {"tenant_id": T, "phone": f"{ME}:proj-7", "role": "user", "text": "c"},      # project thread
            {"tenant_id": T, "phone": OTHER, "role": "user", "text": "someone else"},
            {"tenant_id": "other", "phone": ME, "role": "user", "text": "another business"},
        ],
        "commerce_conversation_sessions": [
            {"id": "s-me", "tenant_id": T, "session_key": ME},
            {"id": "s-me-admin", "tenant_id": T, "session_key": f"admin:{ME}"},
            {"id": "s-other", "tenant_id": T, "session_key": OTHER},
            {"id": "s-elsewhere", "tenant_id": "other", "session_key": ME},
        ],
        "commerce_conversation_messages": [
            {"tenant_id": T, "session_id": "s-me", "role": "user", "content": "order please",
             "created_at": "2026-10-01T08:00:00Z"},
            {"tenant_id": T, "session_id": "s-me", "role": "assistant", "content": "sure",
             "created_at": "2026-10-01T08:00:05Z"},
            {"tenant_id": T, "session_id": "s-me-admin", "role": "user", "content": "sales today?",
             "created_at": "2026-10-02T08:00:00Z"},
            {"tenant_id": T, "session_id": "s-other", "role": "user", "content": "not mine",
             "created_at": "2026-10-01T09:00:00Z"},
            {"tenant_id": "other", "session_id": "s-elsewhere", "role": "user", "content": "elsewhere",
             "created_at": "2026-10-01T09:00:00Z"},
        ],
        "vula_reply_feedback": [{"tenant_id": T, "session_id": "s-me", "message_id": "x"},
                                {"tenant_id": T, "session_id": "s-other", "message_id": "y"}],
        "vula_turns": [{"tenant_id": T, "phone": ME, "text": "t"}, {"tenant_id": T, "phone": OTHER, "text": "t"}],
        "vula_kb_turns": [{"tenant_id": T, "asker": asker, "question": "q"},
                          {"tenant_id": T, "asker": kb_learning.asker_key(T, OTHER), "question": "q"}],
        "vula_open_questions": [{"tenant_id": T, "phone": ME}, {"tenant_id": T, "phone": OTHER}],
        "vula_escalations": [{"tenant_id": T, "customer_phone": ME, "question": "q"},
                             {"tenant_id": T, "customer_phone": OTHER, "question": "q"}],
        "commerce_client_summaries": [{"tenant_id": T, "client_key": ME, "summary": "2026-10-01: asked about hake"},
                                      {"tenant_id": T, "client_key": OTHER, "summary": "someone else"}],
        "commerce_orders": [{"tenant_id": T, "customer_phone": ME, "total_cents": 12000}],
        "commerce_invoices": [{"tenant_id": T, "customer_phone": ME, "total_cents": 12000}],
    })


def test_phone_variants_cover_the_stored_spellings():
    assert set(privacy_data.phone_variants("0821112222")) >= {"0821112222", "27821112222", "+27821112222"}
    assert set(privacy_data.phone_variants("+27 82 111 2222")) >= {"27821112222", "0821112222"}
    assert privacy_data.phone_variants("") == []


def test_erase_removes_every_conversation_store_for_this_number_only():
    db = _db()
    with patch.object(service, "_client", return_value=db):
        out = privacy_data.erase(T, ME)
    assert out["failed"] == []
    assert out["removed"] == {"chat_messages": 3, "conversations": 3, "turn_records": 1,
                              "question_log": 1, "open_questions": 1, "escalations": 1, "client_note": 1,
                              "private_notes": 0}        # a customer: no staff notes to remove
    assert [r["client_key"] for r in db.rows["commerce_client_summaries"]] == ["27820000009"]
    # nothing of theirs is left in this business
    assert [r["phone"] for r in db.rows["vula_chat_messages"]] == [OTHER, ME]       # other person; other business
    assert {r["id"] for r in db.rows["commerce_conversation_sessions"]} == {"s-other", "s-elsewhere"}
    assert {r["session_id"] for r in db.rows["commerce_conversation_messages"]} == {"s-other", "s-elsewhere"}
    assert [r["session_id"] for r in db.rows["vula_reply_feedback"]] == ["s-other"]
    for table, col in (("vula_turns", "phone"), ("vula_open_questions", "phone"),
                       ("vula_escalations", "customer_phone")):
        assert [r[col] for r in db.rows[table]] == [OTHER], table
    assert len(db.rows["vula_kb_turns"]) == 1


def test_erase_keeps_financial_records():
    db = _db()
    with patch.object(service, "_client", return_value=db):
        privacy_data.erase(T, ME)
    assert db.count("commerce_orders") == 1 and db.count("commerce_invoices") == 1


def test_a_store_that_fails_is_reported_and_the_rest_still_run():
    db = _db()
    db.fail.add("vula_turns")
    with patch.object(service, "_client", return_value=db):
        out = privacy_data.erase(T, ME)
    assert out["failed"] == ["turn_records"]
    assert out["removed"]["chat_messages"] == 3 and out["removed"]["escalations"] == 1


def test_erase_with_no_number_or_tenant_touches_nothing():
    db = _db()
    with patch.object(service, "_client", return_value=db):
        assert privacy_data.erase(T, "") == {"removed": {}, "failed": []}
        assert privacy_data.erase("", ME) == {"removed": {}, "failed": []}
    assert db.count("vula_chat_messages") == 5


# ── the reply to the person ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_reply_says_what_was_done_and_makes_no_30_day_promise():
    import vula.api.whatsapp as wa
    send = AsyncMock(return_value=True)
    with patch.object(service, "_client", return_value=_db()), \
         patch("vula.api.commerce.record_opt_out", MagicMock()), \
         patch("vula.models.tenants.get_tenant_db", return_value=MagicMock()), \
         patch.object(wa, "_send_reply", send):
        await wa._handle_data_deletion(ME, T)
    msg = send.await_args.args[1]
    assert "deleted your" in msg and "conversations with us" in msg
    assert "30 days" not in msg
    assert "financial records" in msg


def test_delete_reply_is_honest_when_something_could_not_be_removed():
    import vula.api.whatsapp as wa
    msg = wa._deletion_reply(["contact record"], ["turn_records"])
    assert "couldn't remove everything" in msg and "deleted your" not in msg and "✅" not in msg
    assert "no conversation history" in wa._deletion_reply([], [])


# ── export ──────────────────────────────────────────────────────────────────────────────────

def test_export_reads_the_commerce_conversation_for_this_number_only():
    with patch.object(service, "_client", return_value=_db()):
        msgs = privacy_data.conversation_messages(T, "0821112222")
    assert [(m.role, m.text) for m in msgs] == [("user", "order please"), ("assistant", "sure"),
                                                ("user", "sales today?")]


def test_merge_drops_the_mirrored_copy_and_keeps_order():
    a = [SimpleNamespace(role="user", text="hello", created_at="2026-10-01T08:00:00.100Z")]
    b = [SimpleNamespace(role="user", text="hello", created_at="2026-10-01T08:00:00.900Z"),
         SimpleNamespace(role="assistant", text="hi", created_at="2026-10-01T08:00:02Z")]
    assert [m.text for m in privacy_data.merge_messages(a, b)] == ["hello", "hi"]


@pytest.mark.asyncio
async def test_assembled_export_includes_the_commerce_conversation():
    from vula.api import data_export
    chat = MagicMock()
    chat.get.return_value = [SimpleNamespace(role="user", text="knowledge question", created_at="2026-09-01T08:00:00Z")]
    with patch.object(service, "_client", return_value=_db()), \
         patch("vula.chat.history.get_db", return_value=chat):
        data = await data_export.assemble_export(T, ME)
    texts = [m.text for m in data["messages"]]
    assert texts == ["knowledge question", "order please", "sure", "sales today?"]
