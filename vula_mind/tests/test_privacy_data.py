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
        # vula/listings: the website keeps the number as it was typed
        "vula_properties": [{"id": "p-1", "tenant_id": T, "title": "3 bed house, Durbanville"},
                            {"id": "p-2", "tenant_id": T, "title": "Flat, Bellville"}],
        "vula_property_enquiries": [
            {"id": "e-1", "tenant_id": T, "property_id": "p-1", "name": "Me", "whatsapp": "082 111 2222",
             "message": "Is it still available?", "preferred_times": "Saturday morning",
             "created_at": "2026-10-03T08:00:00Z"},
            {"id": "e-2", "tenant_id": T, "property_id": "p-2", "name": "Me", "whatsapp": "+27 82 111 2222",
             "message": "Pets allowed?", "created_at": "2026-10-04T08:00:00Z"},
            {"id": "e-3", "tenant_id": T, "property_id": "p-1", "name": "Other", "whatsapp": "082 000 0009",
             "message": "not mine", "created_at": "2026-10-03T09:00:00Z"},
            {"id": "e-4", "tenant_id": T, "property_id": "p-1", "name": "Email only", "whatsapp": None,
             "message": "no number", "created_at": "2026-10-03T09:00:00Z"},
            {"id": "e-5", "tenant_id": "other", "name": "Me", "whatsapp": "0821112222", "message": "elsewhere"},
        ],
        "vula_buyer_wishes": [
            {"id": "w-1", "tenant_id": T, "phone": ME, "min_bedrooms": 3, "property_types": ["house"],
             "listing_type": "sale", "suburbs": ["Durbanville"]},
            {"id": "w-2", "tenant_id": T, "phone": OTHER, "property_types": ["flat"]},
            {"id": "w-3", "tenant_id": "other", "phone": ME, "property_types": ["flat"]},
        ],
        "vula_listing_shares": [
            {"id": "sh-1", "tenant_id": T, "phone": ME, "property_id": "p-1", "created_at": "2026-10-02T08:00:00Z"},
            {"id": "sh-2", "tenant_id": T, "phone": OTHER, "property_id": "p-1"},
            {"id": "sh-3", "tenant_id": "other", "phone": ME, "property_id": "p-9"},
        ],
        "vula_deals": [{"id": "d-1", "tenant_id": T, "title": "3 bed house", "contact_phone": ME}],
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
                              "private_notes": 0,        # a customer: no staff notes to remove
                              "research_notes": 0,       # a customer: no research findings either
                              "property_enquiries": 2, "buyer_wish": 1, "listings_sent": 1,
                              # 13 Nov 2026: stores added to the erase (none hold rows in this test)
                              "messages_sent": 0, "contact_record": 0, "broadcasts_received": 0,
                              "delivery_failures": 0, "flow_progress": 0, "pending_confirmations": 0}
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
    assert db.count("vula_deals") == 1          # the business's pipeline record, kept like orders


def test_erase_removes_property_enquiries_typed_any_way_and_the_wish_that_would_keep_matching():
    """Master's listings (migrations 257-259) keep a person's enquiry, what they're looking for
    and the listings sent to them. Left behind, the wish would keep matching them to new
    listings after they asked for their data to be deleted."""
    db = _db()
    with patch.object(service, "_client", return_value=db):
        privacy_data.erase(T, "0821112222")
    assert {r["id"] for r in db.rows["vula_property_enquiries"]} == {"e-3", "e-4", "e-5"}
    assert {r["id"] for r in db.rows["vula_buyer_wishes"]} == {"w-2", "w-3"}
    assert {r["id"] for r in db.rows["vula_listing_shares"]} == {"sh-2", "sh-3"}
    assert db.count("vula_properties") == 2


def test_same_number_matches_the_last_nine_digits_only_of_a_full_number():
    v = privacy_data.phone_variants(ME)
    assert privacy_data.same_number("082 111 2222", v) and privacy_data.same_number("+27 (82) 111-2222", v)
    assert not privacy_data.same_number("082 000 0009", v)
    assert not privacy_data.same_number("2222", v) and not privacy_data.same_number("", v)


def test_a_missing_listings_table_is_reported_not_hidden():
    db = _db()
    db.fail.add("vula_property_enquiries")
    with patch.object(service, "_client", return_value=db):
        out = privacy_data.erase(T, ME)
    assert out["failed"] == ["property_enquiries"]
    assert out["removed"]["buyer_wish"] == 1 and out["removed"]["chat_messages"] == 3


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


def test_export_includes_their_property_records_and_nobody_elses():
    with patch.object(service, "_client", return_value=_db()):
        got = privacy_data.property_records(T, "+27821112222")
    assert got["enquiries"] == [
        {"date": "2026-10-03", "property": "3 bed house, Durbanville", "message": "Is it still available?",
         "preferred_times": "Saturday morning"},
        {"date": "2026-10-04", "property": "Flat, Bellville", "message": "Pets allowed?", "preferred_times": ""},
    ]
    assert got["wish"] == "3+ bed house to buy in Durbanville"
    assert got["listings_sent"] == [{"date": "2026-10-02", "property": "3 bed house, Durbanville"}]


def test_export_property_parts_fail_on_their_own():
    db = _db()
    db.fail.add("vula_property_enquiries")
    with patch.object(service, "_client", return_value=db):
        got = privacy_data.property_records(T, ME)
    assert got["enquiries"] == [] and got["wish"] and len(got["listings_sent"]) == 1


@pytest.mark.asyncio
async def test_the_export_document_has_the_property_sections():
    from vula.api import data_export
    chat = MagicMock()
    chat.get.return_value = []
    with patch.object(service, "_client", return_value=_db()), \
         patch("vula.chat.history.get_db", return_value=chat):
        data = await data_export.assemble_export(T, ME)
    md = data_export._render_export_markdown(ME, data)
    assert "## Property enquiries (2)" in md and "Pets allowed?" in md and "not mine" not in md
    assert "## What you told us you're looking for" in md and "3+ bed house to buy in Durbanville" in md
    assert "## Listings sent to you (1)" in md
