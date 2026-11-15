"""A staff member's private notes (vula/personal_memory.py): saved only when they ask, theirs
alone, shown only to the assistant answering them, never a business fact, and theirs to delete."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config import settings
from core.skills.base import behaviour_preamble
from tests.fake_supabase import FakeSupabase
from vula import identity
from vula import personal_memory as pm
from vula.commerce import service

T = "deli"
THANDI = identity.Person(T, member_id="11111111-1111-1111-1111-111111111111", name="Thandi", role="staff",
                         phone="27821112222")
SIPHO = identity.Person(T, member_id="22222222-2222-2222-2222-222222222222", name="Sipho", role="staff",
                        phone="27823334444")
OWNER = identity.Person(T, user_id="99999999-9999-9999-9999-999999999999", role="owner")     # a login, no team row
ELSEWHERE = identity.Person("other", member_id=THANDI.member_id, name="Same id, another business")


@pytest.fixture()
def db():
    d = FakeSupabase({pm.TABLE: []})
    with patch.object(service, "_client", return_value=d), patch.object(settings, "personal_memory_enabled", True):
        yield d


# ── recognising the request ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,note", [
    ("remember that I prefer short answers", "I prefer short answers"),
    ("Please remember I'm off on Fridays.", "I'm off on Fridays."),
    ("remember my daughter writes matric in November", "my daughter writes matric in November"),
    ("note that I am planning to study part-time next year", "I am planning to study part-time next year"),
    ("remember for me: the Paarl run is mine on Tuesdays", "the Paarl run is mine on Tuesdays"),
    ("onthou dat ek Maandae laat begin", "ek Maandae laat begin"),
])
def test_first_person_and_explicitly_private_requests_are_recognised(text, note):
    assert pm.wants_to_remember(text) == note


@pytest.mark.parametrize("text", [
    "remember we close at 4 on Fridays",              # a business rule — the company-wide tool's job
    "remember that hake is R120 a kilo",
    "remember to send invoice 104",
    "do you remember what I ordered?",
    "what were today's sales?",
    "I remember that I paid already",
    "",
])
def test_business_rules_and_ordinary_messages_are_left_alone(text):
    assert pm.wants_to_remember(text) is None


# ── consent, and the off switch ─────────────────────────────────────────────────────────────

def test_off_by_default_nothing_is_recognised_stored_or_shown():
    assert settings.personal_memory_enabled is False
    d = FakeSupabase({pm.TABLE: []})
    with patch.object(service, "_client", return_value=d):
        assert pm.handle(THANDI, "remember that I prefer short answers") is None
        assert pm.block_for(THANDI) == ""
    assert d.rows[pm.TABLE] == []


def test_nothing_is_saved_from_an_ordinary_message(db):
    for text in ("what were today's sales?", "I prefer mornings by the way", "my son is sick today"):
        assert pm.handle(THANDI, text) is None
    assert db.rows[pm.TABLE] == []


# ── saving ──────────────────────────────────────────────────────────────────────────────────

def test_a_note_is_saved_encrypted_and_confirmed_only_after_reading_it_back(db):
    reply = pm.handle(THANDI, "remember that I prefer short answers")
    assert "just for you" in reply and "I prefer short answers" in reply and "Nobody else" in reply
    row = db.rows[pm.TABLE][0]
    assert row["content"].startswith("fernet:") and "short answers" not in row["content"]
    assert (row["tenant_id"], row["member_id"], row["user_id"], row["source"]) == (T, THANDI.member_id, None, "whatsapp")
    assert [n["text"] for n in pm.notes(THANDI)] == ["I prefer short answers"]


def test_a_save_that_did_not_stick_is_not_reported_as_saved(db):
    with patch.object(pm, "notes", return_value=[]):                  # the read-back finds nothing
        assert pm.remember(THANDI, "I prefer short answers") == {"error": "not_saved"}
        reply = pm.handle(THANDI, "remember that I prefer short answers")
    assert "couldn't save" in reply and "haven't" in reply


def test_an_owner_login_with_no_team_row_can_keep_notes_too(db):
    assert pm.remember(OWNER, "I do the books on Sundays")["saved"]
    row = db.rows[pm.TABLE][0]
    assert row["member_id"] is None and row["user_id"] == OWNER.user_id
    assert [n["text"] for n in pm.notes(OWNER)] == ["I do the books on Sundays"]


def test_duplicates_and_the_limit(db):
    pm.remember(THANDI, "I prefer short answers")
    assert pm.remember(THANDI, "i prefer short answers").get("already") and len(db.rows[pm.TABLE]) == 1
    for i in range(pm.MAX_NOTES - 1):
        assert pm.remember(THANDI, f"I like thing number {i}")["saved"]
    assert pm.remember(THANDI, "I want one more") == {"error": "full"}
    assert "limit" in pm.handle(THANDI, "remember that I want one more")


def test_a_note_is_one_bounded_line(db):
    out = pm.remember(THANDI, "I   like\n\n  long   notes " + "x" * 600)
    assert "\n" not in out["saved"] and len(out["saved"]) <= pm.MAX_NOTE_CHARS


# ── private to the person ───────────────────────────────────────────────────────────────────

def test_nobody_else_can_read_or_delete_a_persons_notes(db):
    pm.remember(THANDI, "I am thinking of applying for the manager job")
    note_id = pm.notes(THANDI)[0]["id"]
    for other in (SIPHO, OWNER, ELSEWHERE, None, identity.Person(T)):
        assert pm.notes(other) == [], other
        assert pm.block_for(other) == ""
        assert pm.forget(other, note_id) is False
        assert pm.forget_all(other) == 0
    assert len(pm.notes(THANDI)) == 1
    assert "Here's what I'm keeping" not in (pm.handle(SIPHO, "what do you remember about me") or "x") \
        or "manager job" not in pm.handle(SIPHO, "what do you remember about me")


def test_the_assistant_sees_the_notes_only_while_answering_that_person(db):
    pm.remember(THANDI, "I prefer bullet points")
    block = pm.block_for(THANDI)
    assert "PRIVATE NOTES" in block and "- I prefer bullet points" in block
    assert "not facts about the business" in block and "never mention them to anyone but this person" in block
    assert "bullet points" not in behaviour_preamble()               # no turn: nothing
    with pm.use(block):
        assert "- I prefer bullet points" in behaviour_preamble(agentic=True)
    assert "bullet points" not in behaviour_preamble()               # and nothing left behind
    with pm.use(pm.block_for(SIPHO)):
        assert "bullet points" not in behaviour_preamble()           # a colleague's turn never has them


def test_the_newest_notes_are_kept_when_there_are_too_many_to_fit(db):
    for i in range(12):
        pm.remember(THANDI, f"I note {i:02d} " + "x" * 120)
    block = pm.block_for(THANDI)
    assert "I note 11" in block and "I note 00" not in block and len(block) < pm.MAX_BLOCK_CHARS + 700


# ── seeing and deleting ─────────────────────────────────────────────────────────────────────

def test_they_can_list_and_forget_one_or_all(db):
    for t in ("I prefer short answers", "I'm off on Fridays", "my car is in for repairs"):
        pm.remember(THANDI, t)
    listing = pm.handle(THANDI, "What do you remember about me?")
    assert "1. I prefer short answers" in listing and "3. my car is in for repairs" in listing and "Only you" in listing
    assert pm.handle(THANDI, "forget note 2") == "Done, I've forgotten: \"I'm off on Fridays\""
    assert [n["text"] for n in pm.notes(THANDI)] == ["I prefer short answers", "my car is in for repairs"]
    assert "I only have 2 notes" in pm.handle(THANDI, "forget note 9")
    assert "all 2 things" in pm.handle(THANDI, "forget everything about me")
    assert pm.notes(THANDI) == []
    assert "not keeping anything" in pm.handle(THANDI, "my notes")
    assert "wasn't keeping anything" in pm.handle(THANDI, "forget everything about me")


def test_a_broken_store_is_admitted_not_papered_over(db):
    db.fail.add(pm.TABLE)
    for text in ("remember that I prefer short answers", "what do you remember about me", "forget note 1"):
        assert "couldn't" in pm.handle(THANDI, text)
    assert pm.handle(THANDI, "what were today's sales?") is None       # ordinary messages still go through
    assert pm.block_for(THANDI) == ""                                  # and a reply is never lost over it


# ── lifecycle ───────────────────────────────────────────────────────────────────────────────

def test_notes_go_when_the_member_leaves_or_asks_for_deletion(db):
    from vula import privacy_data
    pm.remember(THANDI, "I prefer short answers")
    pm.remember(SIPHO, "I start at seven")
    with patch("vula.team_index.member_for_phone",
               side_effect=lambda t, p: {"id": THANDI.member_id} if p.endswith("821112222") else None):
        assert privacy_data._erase_private_notes(T, privacy_data.phone_variants("0821112222")) == 1
    assert pm.notes(THANDI) == [] and len(pm.notes(SIPHO)) == 1
    assert pm.erase_member(T, SIPHO.member_id) == 1 and db.rows[pm.TABLE] == []
    assert "private_notes" in [label for label, _ in privacy_data.ERASE_STEPS]


@pytest.mark.asyncio
async def test_removing_a_team_member_removes_their_notes(db):
    from vula.api import team
    pm.remember(THANDI, "I prefer short answers")
    db.rows["vula_team_members"] = [{"id": THANDI.member_id, "tenant_id": T}]
    with patch.object(team, "_client", return_value=db), patch.object(team.merchant_audit, "audit"):
        await team.remove_member(T, THANDI.member_id, identity={"user_id": "u", "email": "e"})
    assert db.rows[pm.TABLE] == [] and db.rows["vula_team_members"] == []


def test_private_notes_are_covered_by_key_rotation():
    from vula import secret_keys
    assert any(f.table == pm.TABLE and f.col == "content" for f in secret_keys.FIELDS)


# ── the dashboard routes: only ever the caller's own ────────────────────────────────────────

def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


def _as(person):
    return patch("vula.identity.person_for_login", AsyncMock(return_value=person))


@pytest.mark.asyncio
async def test_my_memory_routes_read_add_and_delete_only_my_own(db):
    from vula.api import me
    pm.remember(SIPHO, "I start at seven")
    sipho_note = pm.notes(SIPHO)[0]["id"]
    with _as(THANDI):
        assert (await me.add_my_memory(T, me.NoteIn(text="I prefer short answers"), _req()))["saved"] == "I prefer short answers"
        mine = await me.get_my_memory(T, _req())
        assert mine["enabled"] and [n["text"] for n in mine["notes"]] == ["I prefer short answers"]
        with pytest.raises(HTTPException) as e:                        # a colleague's note id: not found
            await me.forget_my_note(T, sipho_note, _req())
        assert e.value.status_code == 404
        assert (await me.forget_my_note(T, mine["notes"][0]["id"], _req())) == {"ok": True}
        assert (await me.forget_all_my_memory(T, _req())) == {"ok": True, "removed": 0}
    assert [n["text"] for n in pm.notes(SIPHO)] == ["I start at seven"]
    with _as(None), pytest.raises(HTTPException) as e:
        await me.get_my_memory(T, _req())
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_the_routes_say_so_when_the_feature_is_off():
    from vula.api import me
    with _as(THANDI):
        assert await me.get_my_memory(T, _req()) == {"enabled": False, "notes": []}
        with pytest.raises(HTTPException) as e:
            await me.add_my_memory(T, me.NoteIn(text="I prefer short answers"), _req())
    assert e.value.status_code == 404


# ── not left lying around elsewhere ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_private_note_request_in_dashboard_chat_never_reaches_the_history_or_the_agent(db):
    from vula.api import commerce
    skill = AsyncMock()
    with _as(THANDI), patch("core.skills.loader.get_skill", return_value=skill):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(message="remember that I prefer short answers"), _req())
    assert out["private"] is True and "just for you" in out["answer"]
    skill.assert_not_awaited()
    assert "commerce_conversation_messages" not in db.rows and "commerce_conversation_sessions" not in db.rows


@pytest.mark.asyncio
async def test_on_whatsapp_it_is_replied_to_and_the_turn_record_keeps_no_words(db):
    import vula.api.whatsapp as wa
    from vula import turns
    saved = {}
    send = AsyncMock(return_value=True)

    async def handler():
        with patch.object(wa, "_staff_person", return_value=THANDI), patch.object(wa, "_send_reply", send):
            assert await wa._private_note_reply(T, THANDI.phone, "remember that I prefer short answers") is True
            turns.reply("Got it. I'll remember that, just for you: \"I prefer short answers\"")
            assert await wa._private_note_reply(T, THANDI.phone, "what were today's sales?") is False

    with patch.object(turns, "_client", return_value=MagicMock(**{
            "table.return_value.insert.side_effect": lambda row: saved.update(row) or MagicMock()})):
        await turns.run(handler(), tenant_id=T, phone=THANDI.phone, kind="text",
                        text="remember that I prefer short answers")
    assert "just for you" in send.await_args.args[1]
    assert saved["text"] == "[private note]" and "short answers" not in str(saved)


def test_the_operator_sees_that_a_staff_turn_happened_but_not_what_was_said():
    from vula.api import master
    rows = [
        {"tenant_id": T, "phone": "27821112222", "text": "I'm worried about my job", "outcome": "done",
         "replies": [{"kind": "text", "text": "Let's talk it through", "ms": 900}],
         "steps": [{"step": "skill", "name": "reasoning", "ms": 5, "question": "I'm worried about my job"},
                   {"step": "error", "error": "Timeout", "ms": 9}]},
        {"tenant_id": T, "phone": "27820000001", "text": "do you deliver?", "outcome": "done",
         "replies": [{"kind": "text", "text": "Yes", "ms": 400}], "steps": []},
    ]
    with patch("vula.identity.staff_phone_digits", return_value={"27821112222"}):
        staff, customer = master._without_staff_words(rows)
    assert staff["text"] is None and staff["private"] is True and staff["outcome"] == "done"
    assert staff["replies"] == [{"kind": "text", "ms": 900}]
    assert staff["steps"] == [{"step": "skill", "ms": 5, "name": "reasoning"}, {"step": "error", "ms": 9, "error": "Timeout"}]
    assert "worried" not in str(staff) and "talk it through" not in str(staff)
    assert customer["text"] == "do you deliver?" and customer["replies"][0]["text"] == "Yes"
    with patch("vula.identity.staff_phone_digits", side_effect=RuntimeError("db down")):
        assert all(r["text"] is None for r in master._without_staff_words(rows))      # can't tell: hide
