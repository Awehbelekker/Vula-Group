"""A project's client on the business's WhatsApp line (vula/commerce/project_client.py), 28 Oct.

Ian: "can the client do everything we did like we went through Belladonna?" The client asks about
their own project (answered from their view only, by templates), sends slips for suppliers they
paid themselves (held for the owner's yes), and questions a line (handed to the team). A team
member is never a client. All figures made up.
"""
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_job_costing import FakeDB
from vula.commerce import project_client as pc
from vula.commerce import project_tracker as pt
from vula.commerce import service

TID = "digg-demo"


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_projects": [{"tenant_id": TID, "name": "Belladonna", "number": None, "aliases": []},
                                     {"tenant_id": TID, "name": "Porterfield", "number": "2604", "aliases": []}]})
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr("vula.team_index.member_for_phone",
                        lambda tid, phone: {"name": "Judy", "role": "owner"} if phone.endswith("1111111") else None)
    monkeypatch.setattr(pt, "list_shares", lambda tid, project=None: [])
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "DIGG")
    return fake


POS = {
    "project": "Belladonna", "budget_title": "Project bill", "budget_cents": 46_119_500, "sections": [
        {"section": "Main bathroom", "budget_cents": 9_000_000, "actual_cents": 9_500_000, "paid_cents": 9_000_000}],
    "sheet": {"actual_cents": 9_500_000, "paid_cents": 9_000_000}, "variations_claimed_cents": 0,
    "contract_cents": 46_119_500, "trades": [{"trade": "Labour", "cents": 12_345_600}],
    "business_cost_cents": 12_345_600, "paid_directly_cents": 28_381_608, "direct_payments": [
        {"id": "d1", "supplier": "Bathroom Bizarre", "amount_cents": 1_793_606, "paid_on": "2026-10-22", "status": "paid"}],
    "to_pay_directly_cents": 6_427_000, "spent_cents": 40_727_208, "budget_left_cents": 5_392_292,
    "fee_pct": 10.0, "fee_cents": 1_234_560, "open_book_total_cents": 13_580_160, "received_cents": 7_350_000,
    "open_book_balance_cents": 6_230_160, "invoices": [
        {"invoice_number": "DIG-INV-00090", "total_cents": 5_000_000, "paid_cents": 3_000_000, "due_date": "2026-11-01"}],
    "invoiced_cents": 5_000_000, "invoices_paid_cents": 3_000_000, "invoices_owed_cents": 2_000_000,
    "to_invoice_cents": 41_119_500, "claims": None, "programme": None, "documents": [],
    "overhead_share_cents": 99_999, "profit_cents": -77_777, "as_of": "2026-10-28",
}


# ── linking a client ───────────────────────────────────────────────────────

def test_the_owner_links_a_client_and_can_take_them_off(db):
    link = pc.add_client(TID, "belladonna", "082 123 4567", "Rudi Scheepers", "open_book", by="owner")
    assert (link["project"], link["phone"], link["view"], link["name"]) == \
        ("Belladonna", "27821234567", "open_book", "Rudi Scheepers")
    assert [lk["project"] for lk in pc.links_for(TID, "+27 82 123 4567")] == ["Belladonna"]
    again = pc.add_client(TID, "Belladonna", "0821234567", view="price")       # re-adding updates
    assert again["view"] == "price" and len(db.tables["vula_project_clients"]) == 1
    assert pc.remove_client(TID, link["id"]) and pc.links_for(TID, "0821234567") == []


def test_a_client_is_refused_for_an_unknown_project_or_a_team_number(db):
    with pytest.raises(ValueError, match="no project"):
        pc.add_client(TID, "Sea Point Villa", "0821234567")
    with pytest.raises(ValueError, match="on your team"):
        pc.add_client(TID, "Belladonna", "0821111111")
    with pytest.raises(ValueError, match="WhatsApp number"):
        pc.add_client(TID, "Belladonna", "123")


def test_the_view_follows_the_projects_live_link(db, monkeypatch):
    monkeypatch.setattr(pt, "list_shares", lambda tid, project=None: [
        {"view": "open_book", "active": True}, {"view": "price", "active": False}])
    assert pc.add_client(TID, "Belladonna", "0821234567")["view"] == "open_book"


def test_a_client_with_two_projects_names_one():
    links = [{"project": "Belladonna"}, {"project": "Porterfield"}]
    assert pc.pick_link(links, "what's the balance on porterfield?")[0]["project"] == "Porterfield"
    assert pc.pick_link(links, "what's the balance?") == (None, ["Belladonna", "Porterfield"])
    assert pc.pick_link(links[:1], "anything")[0]["project"] == "Belladonna"


# ── what they ask ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("What have I paid so far?", "paid"),
    ("How much do I still owe?", "owe"),
    ("What's the balance?", "owe"),
    ("Where are we on the budget?", "summary"),
    ("What's left to do?", "left"),
    ("When will you finish?", "left"),
    ("What's this R14,228 for?", "query"),
    ("Why is the plumbing so expensive", "query"),
    ("Can I speak to someone", "query"),
    ("Can I speak to Judy please", "ask_team"),                       # still a person answers
    ("Hi", "menu"),
    ("Can you change the tiles to grey?", "ask_team"),
])
def test_what_the_client_asked(text, want):
    assert pc.intent(text) == want


def _ask(db, view, text):
    link = {"id": "c1", "project": "Belladonna", "name": "Rudi Scheepers", "phone": "27821234567", "view": view}
    with patch.object(pt, "position", return_value=dict(POS)):
        return pc.answer(TID, link, text)


def test_price_only_never_shows_the_business_cost_fee_or_profit(db):
    for q in ("what have I paid?", "what's the balance?", "where are we on the budget?"):
        out = _ask(db, "price", q)["reply"]
        for secret in ("123 456.00", "12 345.60", "999.99", "777.77", "Fee", "cost", "Bathroom Bizarre"):
            assert secret not in out, (q, secret, out)
    assert "Owed now: R20 000.00" in _ask(db, "price", "what's the balance?")["reply"]
    assert "DIG-INV-00090: R20 000.00, due 2026-11-01" in _ask(db, "price", "what do I owe?")["reply"]


def test_open_book_shows_the_account(db):
    owe = _ask(db, "open_book", "what's the balance?")["reply"]
    assert "Balance due: R62 301.60" in owe and "Fee (10%): R12 345.60" in owe
    paid = _ask(db, "open_book", "what have I paid?")["reply"]
    assert "To DIGG: R73 500.00" in paid and "Bathroom Bizarre: R17 936.06 (2026-10-22)" in paid
    assert "777.77" not in owe + paid                                   # profit never leaves


def test_what_is_left_comes_from_the_programme(db):
    tasks = [{"title": "Tile guest bathroom", "room": "Guest bathroom", "status": "open", "due_date": "2026-11-03"},
             {"title": "Hang doors", "status": "complete", "due_date": "2026-10-20"}]
    with patch("vula.commerce.project_programme.programme_tasks", return_value=tasks), \
         patch.object(pt, "_programme", return_value={"tasks": 2, "done": 1, "time_pct": 80, "finish": "2026-11-10"}):
        out = _ask(db, "price", "what's left to do?")
    assert "1 of 2 tasks done" in out["reply"] and "Tile guest bathroom (Guest bathroom) — by 2026-11-03" in out["reply"]
    assert "Hang doors" not in out["reply"] and not out["escalate"]
    with patch("vula.commerce.project_programme.programme_tasks", return_value=[]), \
         patch.object(pt, "_programme", return_value=None):
        assert _ask(db, "price", "what's left to do?")["escalate"] is True   # no programme → the team


def test_a_queried_line_goes_to_the_team(db):
    out = _ask(db, "open_book", "What's this R14,228?")
    assert out["escalate"] and "asked the DIGG team" in out["reply"]


# ── a slip they send ───────────────────────────────────────────────────────

def test_a_clients_slip_counts_only_once_the_owner_says_yes(db):
    link = pc.add_client(TID, "Belladonna", "0821234567", "Rudi")
    row = pc.stage_slip(TID, link, supplier="Bathroom Bizarre", amount_cents=1_793_606, paid_on="2026-10-22")
    assert row["status"] == "proposed" and pt.direct_payments(TID, "belladonna") == []
    assert [s["id"] for s in pc.proposed_slips(TID, "Belladonna")] == [row["id"]]
    assert "R17 936.06 paid directly to Bathroom Bizarre on 2026-10-22" in pc.slip_prompt(link, row)
    done = pc.decide_slip(TID, row["id"], approve=True)
    assert done["status"] == "paid"
    assert [p["amount_cents"] for p in pt.direct_payments(TID, "belladonna")] == [1_793_606]
    assert pc.decide_slip(TID, row["id"], approve=False) is None              # already decided


def test_a_rejected_slip_never_counts(db):
    link = pc.add_client(TID, "Belladonna", "0821234567", "Rudi")
    row = pc.stage_slip(TID, link, supplier="Cash Tile", amount_cents=50_000)
    assert pc.decide_slip(TID, row["id"], approve=False)["status"] == "removed"
    assert pt.direct_payments(TID, "belladonna") == []


# ── on WhatsApp ────────────────────────────────────────────────────────────

@pytest.fixture()
def wa(db, monkeypatch):
    from vula.api import whatsapp as w
    sent = []

    async def send(to, text, tenant_id=None, **_k):
        sent.append((to, text))
        return True
    monkeypatch.setattr(w, "_send_reply", send)
    monkeypatch.setattr(w, "_caller_identity",
                        lambda tid, phone: ("Judy", "owner") if phone.endswith("1111111") else (None, None))
    monkeypatch.setattr(w, "_is_tenant_owner", lambda tid, phone: phone.endswith("1111111"))
    return w, sent


async def test_a_linked_client_gets_their_project_and_a_stranger_does_not(wa):
    w, sent = wa
    pc.add_client(TID, "Belladonna", "0821234567", "Rudi", "price")
    with patch.object(pt, "position", return_value=dict(POS)):
        assert await w._maybe_project_client("27821234567", "what do I owe?", TID)
    assert sent[-1][0] == "27821234567" and "Owed now: R20 000.00" in sent[-1][1]
    assert not await w._maybe_project_client("27829999999", "what do I owe?", TID)


async def test_a_client_question_is_handed_to_the_team(wa):
    w, sent = wa
    pc.add_client(TID, "Belladonna", "0821234567", "Rudi", "open_book")
    row = {"helper_phone": "27821111111", "id": "e1"}
    with patch("vula.escalation.create_escalation", return_value=row) as esc, \
         patch("vula.escalation.customer_context", new=AsyncMock(return_value="")):
        assert await w._maybe_project_client("27821234567", "What's this R14,228?", TID)
    assert esc.call_args.args[2].startswith("[Belladonna — client Rudi] What's this R14,228?")
    assert sent[0][0] == "27821111111" and "R14,228" in sent[0][1]          # the team is asked
    assert sent[1] == ("27821234567", "I've asked the DIGG team about that — they'll reply to you here. 🙏")


async def test_the_owner_adds_a_client_on_whatsapp(wa):
    w, sent = wa
    assert await w._maybe_client_setup("27821111111", "add client Rudi Scheepers 082 123 4567 to Belladonna open book", TID)
    assert "is now *Belladonna*'s client" in sent[-1][1] and "Open book" in sent[-1][1]
    assert pc.links_for(TID, "0821234567")[0]["view"] == "open_book"
    # nobody but the owner side can
    assert not await w._maybe_client_setup("27821234567", "add client Me 082 555 1234 to Belladonna", TID)
    assert await w._maybe_client_setup("27821111111", "remove client 082 123 4567 from Belladonna", TID)
    assert pc.links_for(TID, "0821234567") == []


async def test_a_clients_slip_waits_for_the_owner_and_the_client_hears_back(wa, monkeypatch):
    w, sent = wa
    pc.add_client(TID, "Belladonna", "0821234567", "Rudi", "open_book")
    monkeypatch.setattr(w, "_download_media_bytes", AsyncMock(return_value=b"jpeg"))
    monkeypatch.setattr(w, "_read_client_slip", AsyncMock(return_value={
        "amount_cents": 1_793_606, "supplier": "Bathroom Bizarre", "paid_on": "2026-10-22", "reference": None}))
    monkeypatch.setattr(w, "_upload_to_storage", lambda *a, **k: None)
    monkeypatch.setattr(w, "_slip_approvers", lambda tid: ["27821111111"])
    asked = []
    monkeypatch.setattr("vula.open_questions.ask", lambda tid, phone, kind, ref, prompt="": asked.append((phone, kind, ref)))
    monkeypatch.setattr("vula.open_questions.close_for", lambda *a, **k: None)
    assert await w._maybe_project_client_slip("27821234567", "m1", "photo.jpg", "image/jpeg", TID)
    slip = pc.proposed_slips(TID, "Belladonna")[0]
    assert asked == [("27821111111", "client_slip", slip["id"])]
    assert "Reply *yes*" in sent[0][1] and "sent your payment of R17 936.06" in sent[1][1]
    assert pt.direct_payments(TID, "belladonna") == []                       # nothing counts yet
    q = {"id": "q1", "kind": "client_slip", "ref_id": slip["id"]}
    assert await w._answer_client_slip(TID, "27821111111", "yes", q)
    assert [p["amount_cents"] for p in pt.direct_payments(TID, "belladonna")] == [1_793_606]
    assert sent[-1] == ("27821234567", "✅ DIGG confirmed your payment of R17 936.06 to Bathroom Bizarre — "
                                       "it's on *Belladonna* now.")


async def test_a_team_members_photo_is_never_a_client_slip(wa, monkeypatch):
    w, _ = wa
    pc.add_client(TID, "Belladonna", "0821234567", "Rudi")
    monkeypatch.setattr(pc, "links_for", lambda tid, phone: [{"id": "x", "project": "Belladonna", "phone": "27821111111"}])
    assert not await w._maybe_project_client_slip("27821111111", "m1", "photo.jpg", "image/jpeg", TID)
    assert not await w._maybe_project_client("27821111111", "what do I owe?", TID)
