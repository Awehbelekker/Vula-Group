"""Move from Xero, step 1 (5 Nov 2026): connect (signed state, encrypted tokens, refresh), read Xero
within its limits, and preview the move at a cut-over date — nothing written to the books.
Made-up business, contacts and figures throughout; no network.
"""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from vula.migrate import plan, runs
from vula.xero import client as xc
from vula.xero import credentials as cr

T = "seaview-demo"
CUT = date(2026, 10, 31)


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.row, self.up, self.n, self.sort = db, name, [], None, None, None, None, None

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def order(self, col, desc=False): self.sort = (col, desc); return self
    def limit(self, n): self.n = n; return self
    def insert(self, row): self.row = row; return self
    def update(self, patch): self.patch = patch; return self
    def upsert(self, row, on_conflict=None): self.up = (row, on_conflict.split(",")); return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.row is not None:
            new = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
                   "updated_at": datetime.now(timezone.utc).isoformat(), **self.row}
            rows.append(new)
            return SimpleNamespace(data=[dict(new)])
        if self.up:
            row, keys = self.up
            hit = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
            hit.update(row) if hit else rows.append(dict(row))
            return SimpleNamespace(data=[row])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        if self.sort:
            hit = sorted(hit, key=lambda r: str(r.get(self.sort[0]) or ""), reverse=self.sort[1])
        return SimpleNamespace(data=[dict(r) for r in hit][: self.n or None])


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, name): return _Q(self.t, name)


@pytest.fixture()
def db(monkeypatch):
    d = _DB(vula_xero_accounts=[], vula_imports=[], commerce_products=[{"name": "A4 paper box", "sku": "A4"}])
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    monkeypatch.setattr(cr.settings, "xero_client_id", "cid")
    monkeypatch.setattr(cr.settings, "xero_client_secret", "csecret")
    return d


# ── the made-up Xero organisation ────────────────────────────────────────────────────────────

ACCOUNTS = [
    {"AccountID": "a-sales", "Code": "200", "Name": "Sales", "Type": "REVENUE", "Class": "REVENUE"},
    {"AccountID": "a-rent", "Code": "469", "Name": "Rent", "Type": "OVERHEADS", "Class": "EXPENSE"},
    {"AccountID": "a-sundry", "Code": "480", "Name": "Site sundries", "Type": "EXPENSE", "Class": "EXPENSE"},
    {"AccountID": "a-bank", "Code": "090", "Name": "Business cheque", "Type": "BANK", "Class": "ASSET"},
    {"AccountID": "a-ar", "Code": "610", "Name": "Accounts Receivable", "Type": "CURRENT", "Class": "ASSET",
     "SystemAccount": "DEBTORS"},
    {"AccountID": "a-ap", "Code": "800", "Name": "Accounts Payable", "Type": "CURRLIAB", "Class": "LIABILITY",
     "SystemAccount": "CREDITORS"},
    {"AccountID": "a-vat", "Code": "820", "Name": "VAT", "Type": "CURRLIAB", "Class": "LIABILITY", "SystemAccount": "GST"},
    {"AccountID": "a-eq", "Code": "970", "Name": "Owner A Funds Introduced", "Type": "EQUITY", "Class": "EQUITY"},
]


def _tb(rows):
    """A TrialBalance report: rows = [(account id, ytd debit, ytd credit)]."""
    return {"Rows": [{"RowType": "Header", "Cells": [{"Value": "Account"}] * 5},
                     {"RowType": "Section", "Rows": [
                         {"RowType": "Row", "Cells": [{"Value": aid, "Attributes": [{"Id": "account", "Value": aid}]},
                                                      {"Value": "0"}, {"Value": "0"}, {"Value": dr}, {"Value": cr_}]}
                         for aid, dr, cr_ in rows]}]}


TB = _tb([("a-bank", "50000.00", "0"), ("a-ar", "12650.00", "0"), ("a-ap", "0", "2300.00"),
          ("a-vat", "0", "1350.00"), ("a-sales", "0", "90000.00"), ("a-rent", "24000.00", "0"),
          ("a-sundry", "7000.00", "0"), ("a-eq", "0", "0.00")])

INVOICES = [
    # owed in full at the cut-over
    {"InvoiceID": "i1", "Type": "ACCREC", "InvoiceNumber": "INV-0101", "Contact": {"Name": "Oak Street Trust"},
     "DateString": "2026-10-02T00:00:00", "DueDateString": "2026-11-01T00:00:00", "Status": "AUTHORISED",
     "Total": "11500.00", "CurrencyCode": "ZAR"},
    # R2,300 paid before the cut-over, the rest after → R3,450 still owed at the cut-over
    {"InvoiceID": "i2", "Type": "ACCREC", "InvoiceNumber": "INV-0102", "Contact": {"Name": "Main Road Cafe"},
     "DateString": "2026-10-10T00:00:00", "Status": "PAID", "Total": "5750.00", "CurrencyCode": "ZAR",
     "Payments": [{"Date": "/Date(1792108800000+0000)/", "Amount": "2300.00"},      # 16 Oct 2026
                  {"Date": "/Date(1794009600000+0000)/", "Amount": "3450.00"}]},    # 7 Nov 2026
    # paid in full before the cut-over → history
    {"InvoiceID": "i3", "Type": "ACCREC", "InvoiceNumber": "INV-0090", "Contact": {"Name": "Main Road Cafe"},
     "DateString": "2026-09-01T00:00:00", "Status": "PAID", "Total": "1000.00",
     "Payments": [{"Date": "2026-09-15T00:00:00", "Amount": "1000.00"}]},
    # dated after the cut-over → not part of this move
    {"InvoiceID": "i4", "Type": "ACCREC", "InvoiceNumber": "INV-0110", "Contact": {"Name": "Oak Street Trust"},
     "DateString": "2026-11-03T00:00:00", "Status": "AUTHORISED", "Total": "999.00"},
    # a supplier bill still owed
    {"InvoiceID": "b1", "Type": "ACCPAY", "InvoiceNumber": "TC-7781", "Contact": {"Name": "Tile Co (Pty) Ltd"},
     "DateString": "2026-10-20T00:00:00", "Status": "AUTHORISED", "Total": "2300.00"},
]
CREDIT_NOTES = [
    # R2,300 credit: R1,000 set against INV-0101 before the cut-over, R1,300 left
    {"CreditNoteID": "c1", "Type": "ACCRECCREDIT", "CreditNoteNumber": "CN-0007", "Contact": {"Name": "Oak Street Trust"},
     "DateString": "2026-10-25T00:00:00", "Status": "AUTHORISED", "Total": "2300.00",
     "Allocations": [{"Amount": "1000.00", "Date": "2026-10-26T00:00:00", "Invoice": {"InvoiceID": "i1"}}]},
]
CONTACTS = [
    {"ContactID": "k1", "Name": "Oak Street Trust", "IsCustomer": True, "EmailAddress": "accounts@oakstreet.example"},
    {"ContactID": "k2", "Name": "Main Road Cafe", "IsCustomer": True},
    {"ContactID": "k3", "Name": "Tile Co (Pty) Ltd", "IsSupplier": True, "TaxNumber": "4123456789"},
    {"ContactID": "k4", "Name": "Seaview Hardwre", "IsSupplier": True},
    {"ContactID": "k5", "Name": "New Plumbing Supplies", "IsSupplier": True},
]
VULA_SUPPLIERS = [{"id": "s1", "name": "Tile Company", "tax_id": "4123456789"},
                  {"id": "s2", "name": "Seaview Hardware", "aliases": []}]
VULA_CUSTOMERS = [{"id": "c1", "name": "Oak St Trust", "email": "Accounts@OakStreet.example"}]


def _preview():
    return plan.build(org={"Name": "Seaview Builders", "BaseCurrency": "ZAR"}, cutover=CUT, accounts=ACCOUNTS,
                      tb_report=TB, contacts=CONTACTS, items=[{"Name": "A4 paper box"}, {"Name": "Grout 20kg"}],
                      invoices=INVOICES, credit_notes=CREDIT_NOTES, payments=[], bank_transactions=[
                          {"DateString": "2026-10-03T00:00:00"}, {"DateString": "2026-11-02T00:00:00"}],
                      manual_journals=[{}], vula_suppliers=VULA_SUPPLIERS, vula_customers=VULA_CUSTOMERS,
                      vula_products=[{"name": "A4 paper box"}])


# ── preview ──────────────────────────────────────────────────────────────────────────────────

def test_money_and_dates_are_read_exactly():
    assert xc.cents("1234.565") == 123457 and xc.cents("0.1") == 10 and xc.cents(None) == 0
    assert xc.xdate("/Date(1792108800000+0000)/") == date(2026, 10, 16)
    assert xc.xdate("2026-10-02T00:00:00") == date(2026, 10, 2)


def test_accounts_map_to_vula_or_are_kept_as_new():
    m = {a["AccountID"]: plan.map_account(a) for a in ACCOUNTS}
    assert m["a-sales"]["vula_code"] == "sales" and m["a-rent"]["vula_code"] == "rent"
    assert m["a-bank"]["vula_code"] == "bank_cash" and m["a-ap"]["vula_code"] == "accounts_payable"
    assert m["a-ar"]["vula_code"] == "accounts_receivable" and m["a-ar"]["new"]
    assert m["a-vat"]["vula_code"] == "vat_output"
    assert m["a-sundry"]["new"] and m["a-sundry"]["vula_name"] == "Site sundries"
    assert m["a-eq"]["vula_type"] == "equity" and m["a-eq"]["new"]


def test_what_was_owed_is_measured_at_the_cut_over():
    p = _preview()
    rec = {i["number"]: i["owed_cents"] for i in p["receivables"]["items"]}
    assert rec == {"INV-0101": 1050000, "INV-0102": 345000, "CN-0007": -130000}   # 11,500 − 1,000 credit
    assert p["receivables"]["total_cents"] == 1265000
    assert p["receivables"]["agrees"] and p["receivables"]["xero_debtors_cents"] == 1265000
    assert [i["number"] for i in p["payables"]["items"]] == ["TC-7781"]
    assert p["payables"]["total_cents"] == 230000 and p["payables"]["agrees"]
    assert p["history"]["documents"] == 1 and p["history"]["bank_transactions"] == 1


def test_a_difference_from_xero_s_debtors_is_shown_not_hidden():
    bad = _tb([("a-ar", "12000.00", "0"), ("a-bank", "0", "12000.00")])
    p = plan.build(org={}, cutover=CUT, accounts=ACCOUNTS, tb_report=bad, contacts=[], items=[], invoices=INVOICES,
                   credit_notes=CREDIT_NOTES, payments=[], bank_transactions=[], manual_journals=[],
                   vula_suppliers=[], vula_customers=[], vula_products=[])
    assert p["receivables"]["agrees"] is False and p["receivables"]["xero_debtors_cents"] == 1200000


def test_the_opening_trial_balance_balances_and_rolls_up_to_vula_accounts():
    p = _preview()
    assert p["trial_balance"] == {"debits_cents": 9365000, "credits_cents": 9365000, "balanced": True}
    ob = {r["vula_code"]: r["balance_cents"] for r in p["opening_balances"]}
    assert ob["bank_cash"] == 5000000 and ob["sales"] == -9000000 and ob["accounts_payable"] == -230000
    unbalanced = plan.build(org={}, cutover=CUT, accounts=ACCOUNTS, tb_report=_tb([("a-bank", "10.00", "0")]),
                            contacts=[], items=[], invoices=[], credit_notes=[], payments=[], bank_transactions=[],
                            manual_journals=[], vula_suppliers=[], vula_customers=[], vula_products=[])
    assert not unbalanced["trial_balance"]["balanced"] and "doesn't balance" in unbalanced["warnings"][0]


def test_contacts_are_matched_never_duplicated():
    p = _preview()
    assert p["suppliers"]["same"] == 1                                  # Tile Co by VAT number
    assert p["suppliers"]["probably"] == 1 and p["suppliers"]["check"][0]["vula_name"] == "Seaview Hardware"
    assert p["suppliers"]["new"] == 1
    assert p["customers"]["same"] == 1                                  # by email
    assert p["items"] == {"same": 1, "new": 1}
    assert plan.match_contact({"Name": "TILE COMPANY"}, VULA_SUPPLIERS, supplier=True)["match"] == "same"


def test_a_credit_note_refunded_before_the_cut_over_isn_t_open():
    refund = [{"CreditNote": {"CreditNoteID": "c1"}, "Date": "2026-10-28T00:00:00", "Amount": "1300.00"}]
    opens = plan.open_items(INVOICES, CREDIT_NOTES, refund, CUT)
    assert not any(i.get("credit_note") for i in opens["receivables"])


# ── connecting ───────────────────────────────────────────────────────────────────────────────

def test_the_sign_in_state_is_signed_and_expires(db):
    s = cr.sign_state(T, "Sam")
    assert cr.verify_state(s) == {"tenant_id": T, "by": "Sam"}
    body, sig = s.split(".")
    forged = cr._b64(json.dumps({"t": "oakstreet-demo", "b": "", "i": int(datetime.now().timestamp()), "n": "x"}).encode())
    assert cr.verify_state(f"{forged}.{sig}") is None
    assert cr.verify_state(T) is None and cr.verify_state("") is None
    old = cr.sign_state(T, now=datetime.now().timestamp() - 3600)
    assert cr.verify_state(old) is None
    assert "state=" in cr.authorize_url(T) and "accounting.invoices.read" in cr.authorize_url(T)


def test_tokens_are_stored_encrypted_and_refreshed(db, monkeypatch):
    monkeypatch.setattr("vula.email_imap.credentials.encrypt_secret", lambda v: f"enc:{v}" if v else v)
    monkeypatch.setattr("vula.email_imap.credentials.decrypt_secret", lambda v: v[4:] if v and v.startswith("enc:") else v)
    cr.store_connection(T, {"access_token": "acc1", "refresh_token": "ref1", "expires_in": 1800,
                            "xero_tenant_id": "xt-1", "org_name": "Seaview Builders"}, "Sam")
    row = db.t["vula_xero_accounts"][0]
    assert row["access_token"] == "enc:acc1" and row["refresh_token"] == "enc:ref1"
    assert cr.connection(T)["org_name"] == "Seaview Builders"
    assert asyncio.run(cr.access(T)) == {"access_token": "acc1", "xero_tenant_id": "xt-1"}
    row["token_expiry"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sent = []

    async def token(data):
        sent.append(data)
        return {"access_token": "acc2", "refresh_token": "ref2", "expires_in": 1800}
    monkeypatch.setattr(cr, "_token_request", token)
    assert asyncio.run(cr.access(T))["access_token"] == "acc2"
    assert sent[0] == {"grant_type": "refresh_token", "refresh_token": "ref1"}
    assert row["refresh_token"] == "enc:ref2"                           # Xero rotates it

    async def refused(data):
        raise RuntimeError("Xero sign-in refused (400)")
    monkeypatch.setattr(cr, "_token_request", refused)
    row["token_expiry"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(LookupError):
        asyncio.run(cr.access(T))
    assert row["status"] == "needs_reconnect"


def test_the_reader_pages_and_waits_when_xero_asks(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.params.get("page"))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        page = int(request.url.params.get("page"))
        n = 100 if page == 1 else 7
        return httpx.Response(200, json={"Contacts": [{"ContactID": f"{page}-{i}"} for i in range(n)]})

    async def access(tid):
        return {"access_token": "t", "xero_tenant_id": "xt"}
    monkeypatch.setattr(cr, "access", access)
    got = asyncio.run(xc.XeroClient(T, transport=httpx.MockTransport(handler)).contacts())
    assert len(got) == 107 and calls == ["1", "1", "2"]


def test_a_preview_writes_nothing_but_its_own_run(db, monkeypatch):
    async def suppliers(t): return VULA_SUPPLIERS
    async def clients(t, kind=None): return VULA_CUSTOMERS
    monkeypatch.setattr("vula.commerce.service.list_suppliers", suppliers)
    monkeypatch.setattr("vula.commerce.service.list_invoice_clients", clients)

    class Fake:
        calls = 12
        async def organisation(self): return {"Name": "Seaview Builders"}
        async def accounts(self): return ACCOUNTS
        async def trial_balance(self, on): return TB
        async def contacts(self): return CONTACTS
        async def items(self): return []
        async def invoices(self): return INVOICES
        async def credit_notes(self): return CREDIT_NOTES
        async def payments(self): return []
        async def bank_transactions(self): return []
        async def manual_journals(self): return []

    run = db.table("vula_imports").insert({"tenant_id": T, "source": "xero", "cutover_date": CUT.isoformat(),
                                           "status": "reading"}).execute().data[0]
    before = {k: len(v) for k, v in db.t.items()}
    asyncio.run(runs.run_preview(T, run["id"], CUT, client=Fake()))
    got = runs.get(T, run["id"])
    assert got["status"] == "previewed" and got["preview"]["receivables"]["agrees"]
    assert got["preview"]["xero_calls"] == 12
    assert {k: len(v) for k, v in db.t.items()} == before                 # nothing else written
    assert runs.get("oakstreet-demo", run["id"]) is None                  # another business can't see it


def test_a_failed_read_says_why_and_a_stale_one_says_start_again(db):
    class Broken:
        async def organisation(self): raise LookupError("Xero refused Vula's sign-in — connect Xero again")
    run = db.table("vula_imports").insert({"tenant_id": T, "source": "xero", "cutover_date": CUT.isoformat(),
                                           "status": "reading"}).execute().data[0]
    asyncio.run(runs.run_preview(T, run["id"], CUT, client=Broken()))
    assert runs.get(T, run["id"])["error"].startswith("Xero refused")
    stale = db.table("vula_imports").insert({"tenant_id": T, "source": "xero", "cutover_date": CUT.isoformat(),
                                             "status": "reading"}).execute().data[0]
    db.t["vula_imports"][-1]["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert runs.get(T, stale["id"])["status"] == "failed"


def test_the_cut_over_date_must_be_real_and_not_in_the_future():
    assert runs.parse_cutover("2026-10-31") == CUT
    for bad in ("", "31/10/2026", (date.today() + timedelta(days=2)).isoformat()):
        with pytest.raises(ValueError):
            runs.parse_cutover(bad)


def test_the_callback_refuses_a_bad_state_and_connect_needs_vula_s_app(db, monkeypatch):
    from vula.api import xero as api
    page = asyncio.run(api.xero_callback(code="c", state="seaview-demo"))
    assert b"expired" in page.body
    monkeypatch.setattr(cr.settings, "xero_client_secret", "")
    from vula import connections
    connections._TOLD.clear()

    async def alert(text, log_text=True): return True
    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", alert)

    async def ok(request, tid): return True
    monkeypatch.setattr("vula.api.commerce._may_apply_stock", ok)
    out = asyncio.run(api.xero_connect(T, api.ByIn(by="Sam"), SimpleNamespace(headers={})))
    assert out["available"] is False and "Xero" in out["error"]


def test_a_good_callback_saves_the_organisation(db, monkeypatch):
    from vula.api import xero as api
    monkeypatch.setattr("vula.email_imap.credentials.encrypt_secret", lambda v: v)

    async def exchange(code):
        return {"access_token": "a", "refresh_token": "r", "expires_in": 1800, "xero_tenant_id": "xt-9",
                "org_name": "Seaview Builders"}
    monkeypatch.setattr(cr, "exchange_code", exchange)
    page = asyncio.run(api.xero_callback(code="c", state=cr.sign_state(T, "Sam")))
    assert "Seaview Builders" in page.body.decode()
    assert db.t["vula_xero_accounts"][0]["connected_by"] == "Sam"
