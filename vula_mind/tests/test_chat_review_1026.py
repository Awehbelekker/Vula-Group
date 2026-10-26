"""DIGG's WhatsApp, 26 Oct: the replies and bookings Ian flagged as wrong.

Ian sent a batch of Belladonna slips (Bathroom Bizarre, Jackhammer, Pudlo) in under two minutes.
Three photos of one Pudlo slip ran at once and it was booked twice; a Bathroom Bizarre card slip
became "bank charges, personal card"; a tile supplier became "casual labour"; years came back as
2015/2020/2022; a caption naming the project was ignored; and "track all payment for Rudolf on the
statements" searched documents instead of the bank.
"""
import uuid
from unittest.mock import AsyncMock, patch

from vula.api import whatsapp as wa


def _msg(run, i, kind, phone="27645755210"):
    m = {"from": phone, "id": f"wamid.{run}.{i}", "type": kind}
    if kind == "document":
        m["document"] = {"id": f"media{i}", "mime_type": "application/pdf", "filename": f"slip{i}.pdf"}
    else:
        m[kind] = {"id": f"media{i}", "mime_type": "image/jpeg"}
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "pn1"}, "messages": [m]}}]}]}


def test_one_senders_photos_and_documents_run_one_at_a_time_on_a_knowledge_line():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(wa.router, prefix="/v1/whatsapp")
    queued = []

    async def recorder(tenant_id, phone, coro, *, forwarded=False):
        queued.append((tenant_id, phone, forwarded))
        coro.close()

    run = uuid.uuid4().hex
    with patch.object(wa.settings, "vula_fb_app_secret", ""), patch.object(wa.settings, "debug", True), \
         patch.object(wa, "_resolve_number_route", return_value=("digg-demo", "knowledge")), \
         patch("vula.api.tenants.is_active", return_value=True), \
         patch.object(wa, "_is_team", return_value=True), \
         patch.object(wa, "_is_tenant_owner", return_value=True), \
         patch.object(wa, "_mark_read_and_typing", new=AsyncMock()), \
         patch.object(wa, "_one_at_a_time", new=recorder):
        c = TestClient(app)
        for i, kind in enumerate(("image", "image", "document")):
            assert c.post("/v1/whatsapp/webhook", json=_msg(run, i, kind)).status_code == 200
    assert queued == [("digg-demo", "27645755210", False)] * 3


def test_a_card_slip_is_not_assumed_personal_when_no_company_card_is_registered(monkeypatch):
    from vula.commerce import expenses
    monkeypatch.setattr(expenses, "list_cards", lambda t: [])
    assert expenses.resolve_paid_with("digg-demo", card_last4="461264******2708") is None   # → ask
    monkeypatch.setattr(expenses, "list_cards", lambda t: [{"last4": "2708"}])
    assert expenses.resolve_paid_with("digg-demo", card_last4="461264******2708") == "company_card"
    assert expenses.resolve_paid_with("digg-demo", card_last4="****1111") == "personal"


def test_a_caption_naming_the_project_files_the_invoice_there(monkeypatch):
    from vula.integrations import project_resolver
    calls = []

    def resolve(tid, fields, text):
        calls.append(text)
        return {"project": "Belladonna", "kind": "named"} if "belladonna" in text.lower() else None

    monkeypatch.setattr(project_resolver, "resolve", resolve)
    assert wa._project_named_in_caption(
        "digg-demo", "Belladonna and DIGG had to topup the amount on the slip.jpg") == "Belladonna"
    assert wa._project_named_in_caption("digg-demo", "This was for tiles.jpg") is None
    assert wa._project_named_in_caption("digg-demo", "image-wamid.HBgL.jpg") is None   # no caption
    assert calls == ["Belladonna and DIGG had to topup the amount on the slip", "This was for tiles"]


def test_a_card_slip_asks_whose_card_when_vula_cannot_tell(monkeypatch):
    from vula import doc_steps
    from vula.commerce import expenses
    monkeypatch.setattr(expenses, "list_cards", lambda t: [])
    base = {"status": "submitted", "project": "Belladonna", "purpose_category": None}
    assert doc_steps.expense_step("digg-demo", {**base, "paid_with": None, "card_last4": "2708"}) \
        == "expense_paid_with"
    assert doc_steps.expense_step("digg-demo", {**base, "paid_with": None, "card_last4": None}) \
        == "expense_purpose"                       # a cash slip with no card: nothing to ask


def test_ok_keeps_a_guessed_project_and_a_name_moves_it(monkeypatch):
    claim = {"id": "c1", "amount_cents": 90800, "project": "HPC Bokaap"}
    moved = []

    class _Q:
        def select(self, *a): return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[dict(claim)])

    class _DB:
        def table(self, n): return _Q()

    from vula.commerce import expenses
    monkeypatch.setattr("vula.commerce.service._client", lambda: _DB())
    monkeypatch.setattr(expenses, "match_project", lambda t, x: "Belladonna" if "bella" in x.lower() else None)
    monkeypatch.setattr(expenses, "assign", lambda t, cid, project="", **k: moved.append(project) or {})
    assert wa._answer_expense_question("digg-demo", "expense_project", "c1", "OK") == "👍 Kept on *HPC Bokaap*."
    assert not moved
    wa._answer_expense_question("digg-demo", "expense_project", "c1", "Belladonna")
    assert moved == ["Belladonna"]


def test_a_tile_suppliers_slip_is_never_booked_as_labour():
    from vula.commerce.expenses import _materials_not_labour
    accounts = [{"code": "casual_labour"}, {"code": "cost_of_sales"}, {"code": "wages"}]
    pudlo = ("PUDLO S.A (PTY) LTD This is a tax invoice from Pudlo S.A (Pty) Ltd to Digg for building "
             "materials, specifically MAPEI ULTRACOLOR PLUS 100 WHITE 5KG")
    assert _materials_not_labour("casual_labour", pudlo, accounts) == "cost_of_sales"
    assert _materials_not_labour("casual_labour", "Nelitho wages week 40", accounts) == "casual_labour"
    assert _materials_not_labour("casual_labour", "Tiler: labour to lay tiles", accounts) == "casual_labour"
    assert _materials_not_labour("fuel", pudlo, accounts) == "fuel"          # only labour is corrected


def test_a_fresh_slips_misread_year_is_put_right():
    from datetime import date
    from vula.commerce.expenses import fresh_slip_date
    today = date(2026, 10, 26)
    assert fresh_slip_date("2015-10-26", today) == "2026-10-26"     # Pudlo R14,228.45
    assert fresh_slip_date("2020-10-20", today) == "2026-10-20"     # Pudlo grout
    assert fresh_slip_date("2026-10-23", today) == "2026-10-23"     # read right — untouched
    assert fresh_slip_date("2022-04-11", today) == "2022-04-11"     # no recent 11 Apr — left for a person
    assert fresh_slip_date("2025-11-03", today) == "2025-11-03"     # a year out can be real
    assert fresh_slip_date("2024-12-15", date(2026, 1, 10)) == "2025-12-15"   # across New Year
    assert fresh_slip_date("not a date", today) == "not a date"
