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


def test_payment_questions_about_the_statements_go_to_the_admin_agent():
    from core.skills.base import looks_like_owner_admin_question as owner_q
    assert owner_q("Can you track all payment for Rudolf on the statments")
    assert owner_q("Please give me all payment recieved from a Rudolf on the statments")
    assert owner_q("What has Rudi paid us")
    assert not owner_q("belladonna")
    assert not owner_q("Send me the tile spec sheet")


class _BankQ:
    def __init__(self, rows, f):
        self.rows, self.f = rows, f

    def select(self, *a): return self
    def eq(self, k, v): self.f.append(("eq", k, v)); return self
    def neq(self, *a): return self
    def lte(self, *a): return self
    def gte(self, *a): return self
    def ilike(self, k, pat): self.f.append(("ilike", k, pat)); return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self

    def execute(self):
        from types import SimpleNamespace
        pats = [p.strip("%").lower() for t, k, p in self.f if t == "ilike"]
        if not pats:
            return SimpleNamespace(data=[{"txn_date": "2026-10-17"}])
        return SimpleNamespace(data=[r for r in self.rows if pats[0] in r["description"].lower()])


def test_bank_payments_lists_rudolfs_payment_from_the_statement(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    rows = [{"id": "b1", "txn_date": "2026-09-17", "amount_cents": 3750000, "direction": "in",
             "description": "Payshap Credit 4Deb4 Rudolf Scheepe", "project": "Belladonna"},
            {"id": "b2", "txn_date": "2026-09-22", "amount_cents": 840650, "direction": "out",
             "description": "Payshap Account Off-Us Rudi Project", "project": "Belladonna"}]

    class _DB:
        def table(self, n): return _BankQ(rows, [])

    monkeypatch.setattr("vula.commerce.service._client", lambda: _DB())
    out = CommerceAdminSkill()._bank_payments("digg-demo", {"name": "Rudolf Scheepers"})
    assert out["received_cents"] == 3750000 and out["lines"] == 1
    assert "2026-09-17  +R37,500.00  Payshap Credit 4Deb4 Rudolf Scheepe · Belladonna" in out["reply_verbatim"]
    assert "run to 2026-10-17" in out["reply_verbatim"]
    none = CommerceAdminSkill()._bank_payments("digg-demo", {"name": "Nobody"})
    assert none["reply_verbatim"].startswith("Nothing on the bank statements mentions *Nobody*")


def test_bank_payments_is_read_only():
    from core import dry_run
    assert "bank_payments" in dry_run.READ_ONLY


import pytest


class _Rows:
    def __init__(self, data): self.data, self.upd = data, []
    def select(self, *a): return self
    def eq(self, *a): return self
    def limit(self, *a): return self
    def update(self, row): self.upd.append(row); return self
    def execute(self):
        from types import SimpleNamespace
        return SimpleNamespace(data=self.data)


@pytest.mark.asyncio
async def test_a_pdf_boq_reread_never_replaces_a_spreadsheet_budget(monkeypatch, tmp_path):
    from vula.commerce import reread, service
    filed = _Rows([{"filename": "Belladonna Flat – project bill variance 2026-10-26.xlsx"}])

    class _DB:
        def table(self, n): return filed

    upserts = []
    monkeypatch.setattr(service, "_client", lambda: _DB())
    monkeypatch.setattr(service, "upsert_project_boq", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(reread, "_download", AsyncMock(return_value=b"%PDF"))

    async def more_lines(result, path, text, filename):
        return {"fields": {"line_items": [{"description": f"l{i}", "total_cents": 100} for i in range(9)],
                           "sections": [{"section": "Main Bathroom", "budget_cents": 990000}]}}

    monkeypatch.setattr("vula.api.whatsapp._complete_boq_lines", more_lines)

    class _Pages:
        async def parse(self, path): return [(1, "page text")]

    class _Pipe:
        def __init__(self, **k): self.parser = _Pages()

    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _Pipe)
    row = {"id": "d1", "filename": "Bill of Quantities 20261026-1002.pdf", "project": "Belladonna",
           "file_url": "https://x/doc.pdf", "fields": {"line_items": [{"description": "P&G"}]}}
    assert await reread._reread_boq("digg-demo", row) is True      # the PDF's own record is fuller
    assert upserts == []                                            # but the budget stays the sheet's
    filed.data = []                                                 # no spreadsheet filed → PDF may set it
    row["fields"] = {"line_items": [{"description": "P&G"}]}
    assert await reread._reread_boq("digg-demo", row) is True
    assert len(upserts) == 1


def test_a_sheet_with_no_recognised_header_says_what_it_saw(tmp_path, caplog):
    import logging
    from openpyxl import Workbook
    from vula.ingestion import boq_sheet
    wb = Workbook()
    ws = wb.active
    for r in (["Belladonna Flat"], ["Thing", "Number", "Other"], ["Tiling", 14000, 9000]):
        ws.append(r)
    p = tmp_path / "bill.xlsx"
    wb.save(p)
    with caplog.at_level(logging.INFO, logger="vula.ingestion.boq_sheet"):
        assert boq_sheet.parse(p) == []
    assert "no header row recognised" in caplog.text and "Thing; Number; Other" in caplog.text


def test_cost_statement_wordings_are_read(tmp_path):
    from openpyxl import Workbook
    from vula.ingestion import boq_sheet
    wb = Workbook()
    ws = wb.active
    for r in (["Work item", "Budget allowance (R)", "Spent to date"], ["Main Bathroom"],
              ["Tiles + waterproofing", 8400, 9120.5]):
        ws.append(r)
    p = tmp_path / "bill.xlsx"
    wb.save(p)
    (li,) = boq_sheet.parse(p)
    assert (li["description"], li["section"], li["total_cents"], li["actual_cents"]) == \
        ("Tiles + waterproofing", "Main Bathroom", 840000, 912050)


# ── Ian, 26 Oct: "if a staff member scans an invoice or receipt it should ask paid-by options" ──

def test_a_staff_members_slip_always_asks_who_paid(monkeypatch):
    from vula import doc_steps
    from vula.commerce import expenses
    monkeypatch.setattr(expenses, "list_cards", lambda t: [])
    base = {"status": "submitted", "project": "Belladonna", "purpose_category": None, "card_last4": None}
    staff = {**base, "paid_with": None, "reimbursable": True}
    owner = {**base, "paid_with": None, "reimbursable": False}
    assert doc_steps.expense_step("digg-demo", staff) == "expense_paid_with"
    assert doc_steps.expense_step("digg-demo", owner) == "expense_purpose"     # the owner isn't nagged
    assert "*3* the client paid · *4* petty cash" in doc_steps._EXPENSE_PROMPTS["expense_paid_with"]
    assert doc_steps.expense_step("digg-demo", {**staff, "paid_with": "client", "status": "cancelled"}) is None


def test_who_paid_answers():
    for reply, want in (("1", "company_card"), ("2", "personal"), ("3", "client"), ("4", "petty_cash"),
                        ("company card", "company_card"), ("EFT from the business", "company_card"),
                        ("my own money", "personal"), ("the client paid", "client"),
                        ("Rudi the customer paid it", "client"), ("petty cash", "petty_cash"),
                        ("cash", "cash"), ("Belladonna", None), ("12", None)):
        assert wa.paid_with_answer(reply.lower()) == want, reply


def _claim_db(row):
    from types import SimpleNamespace

    class Q:
        def __init__(self): self.patch = None
        def select(self, *a): return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def update(self, p): self.patch = p; return self
        def execute(self):
            if self.patch:
                row.update(self.patch)
            return SimpleNamespace(data=[row])

    return SimpleNamespace(table=lambda n: Q())


def test_a_client_paid_slip_leaves_the_books_but_stays_filed(monkeypatch):
    from vula.commerce import ledger
    row = {"id": "e1", "tenant_id": "digg-demo", "amount_cents": 1793606, "status": "submitted",
           "project": "Belladonna", "paid_with": None, "reimbursable": True}
    monkeypatch.setattr("vula.commerce.service._client", lambda: _claim_db(row))
    reversed_ = []
    monkeypatch.setattr(ledger, "reverse_expense", lambda t, e: reversed_.append(e["id"]))
    out = wa._answer_expense_question("digg-demo", "expense_paid_with", "e1", "3")
    assert (row["status"], row["paid_with"], row["reimbursable"]) == ("cancelled", "client", False)
    assert reversed_ == ["e1"]
    assert "client paid that *R17,936.06* directly" in out and "stays filed on *Belladonna*" in out


def test_petty_cash_and_company_are_never_owed_back(monkeypatch):
    for answer, want in (("4", "petty_cash"), ("1", "company_card")):
        row = {"id": "e1", "amount_cents": 25000, "status": "submitted", "paid_with": None, "reimbursable": True}
        monkeypatch.setattr("vula.commerce.service._client", lambda: _claim_db(row))
        wa._answer_expense_question("digg-demo", "expense_paid_with", "e1", answer)
        assert (row["paid_with"], row["reimbursable"]) == (want, False)


def test_reversing_an_expense_mirrors_its_posting(monkeypatch):
    from vula.commerce import ledger
    posted = []
    monkeypatch.setattr(ledger, "_post", lambda t, **k: posted.append(k))
    ledger.reverse_expense("digg-demo", {"id": "e1", "amount_cents": 11500, "vat_cents": 1500,
                                         "account_code": "cost_of_sales", "date": "2026-10-22"})
    (p,) = posted
    assert p["source_type"] == "expense_reversal" and p["source_id"] == "e1"
    assert sum(li["debit_cents"] for li in p["lines"]) == sum(li["credit_cents"] for li in p["lines"]) == 11500
    assert {"account_code": "bank_cash", "debit_cents": 11500, "credit_cents": 0} in p["lines"]


# ── Ian, 26 Oct: misspelt project names ("Beladona", "Belladona" on DIGG's bank lines) ──

def test_a_misspelt_project_is_still_found():
    from vula.commerce import expenses
    projects = ["Belladonna", "HPC Bokaap", "Atlantis Paarden Eiland", "Porterfield"]
    assert expenses.match_project("digg-demo", "Beladona tiles", projects) == "Belladonna"
    assert expenses.match_project("digg-demo", "for the porterfeild job", projects) == "Porterfield"
    assert expenses.match_project("digg-demo", "atlantic seaboard", projects) is None
    assert expenses.match_project("digg-demo", "bellville", projects) is None
    # Two projects equally close: ask, never pick one.
    assert expenses.match_project("digg-demo", "Beladona", ["Belladonna", "Beladonna Flats"]) is None


def test_the_resolver_reads_a_misspelt_project_in_a_caption():
    from vula.integrations import project_resolver
    projects = [{"name": "Belladonna", "ids": {"belladonna"}, "words": {"belladonna"}},
                {"name": "HPC Bokaap", "ids": {"hpc bokaap"}, "words": {"hpc"}}]
    assert project_resolver._named(projects, "Belladona top up on the slip") == ["Belladonna"]
    assert project_resolver._named(projects, "Belladonna and HPC") == ["Belladonna", "HPC Bokaap"]
    assert project_resolver._named(projects, "Bella's toilet") == []
