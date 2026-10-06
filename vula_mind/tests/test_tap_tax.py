"""Tax invoice on request: VAT maths, buyer parsing, WhatsApp flow, receipt-page endpoints, PDF."""
import re

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as hst

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from tests.test_tap_receipt import CUST, T, pay  # noqa: F401  (shared payment helper)
from vula.api.server import app
from vula.tap import api as tap_api
from vula.tap import receipt as rc
from vula.tap.core import taxinvoice as ti
from vula.tap.service import TapConfig, TapService

SUPPLIER = {"vat_number": "4999999999", "vat_registered": True, "company_name": "Surf Shack (Pty) Ltd",
            "registered_address": "1 Beach Rd, Muizenberg, 7945"}


# ── pure rules ───────────────────────────────────────────────────────────────────────────────
@given(hst.integers(min_value=0, max_value=10**9))
def test_vat_split_always_sums_to_the_bill(bill):
    excl, vat = ti.vat_split(bill)
    assert excl + vat == bill and 0 <= vat <= bill
    assert abs(vat * 115 - bill * 15) <= 57 + 115      # within a rounding step of exactly 15/115


@pytest.mark.parametrize("bill,excl,vat", [(11500, 10000, 1500), (50000, 43478, 6522), (115, 100, 15), (0, 0, 0)])
def test_vat_split_examples(bill, excl, vat):
    assert ti.vat_split(bill) == (excl, vat)


@pytest.mark.parametrize("raw,ok", [("4123456789", True), ("4 123 456 789", True), ("4123-456-789", True),
                                    ("3123456789", False), ("412345678", False), ("41234567890", False), ("", False),
                                    ("abc", False)])
def test_vat_number_validation(raw, ok):
    assert (ti.normalise_vat(raw) is not None) is ok


@pytest.mark.parametrize("text,name,vat", [
    ("Acme Trading (Pty) Ltd, VAT 4123456789", "Acme Trading (Pty) Ltd", "4123456789"),
    ("4123456789 Acme", "Acme", "4123456789"),
    ("Company: Acme CC\nVAT no: 4 123 456 789", "Acme CC", "4123456789"),
    ("Acme only", "Acme only", None),
    ("4123456789", None, "4123456789"),
    ("", None, None)])
def test_parse_details(text, name, vat):
    assert ti.parse_details(text) == (name, vat)


def test_clean_text_strips_markup():
    assert "<" not in ti.clean_text("<script>x</script> Acme", 50) and "{" not in ti.clean_text("a{{7*7}}", 50)


def test_number_format():
    assert ti.format_number(7) == "TI-000007"


# ── service / WhatsApp ───────────────────────────────────────────────────────────────────────
@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    repo = MemoryRepo(clock)
    repo.members[T] = [{"id": "m1", "name": "Sipho Dlamini", "whatsapp": "27800000001", "role": "staff"}]
    repo.settings[T] = {"tenant_id": T, "mode": "live"}
    repo.rules[(T, "m1")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.vat[T] = {"vat_number": "4999999999", "vat_registered": True}
    repo.invoice_cfg[T] = dict(SUPPLIER)
    msg, gw = FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(
        pepper="p", public_base_url="https://api.test", encrypt=lambda x: "enc:" + x, decrypt=lambda x: x[4:],
        clock=clock, enabled=tap_api.tenant_enabled, receipt_secret="rs", receipt_base_url="https://dash.test"))
    tag = repo.add_tag(T, "tag-m1", bound_id="m1")
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    tap_api._mode_cache.clear()
    return svc, repo, msg, gw, tag


def docs(msg, phone=CUST):
    return msg.to(phone, "document")


def texts(msg, phone=CUST):
    return [m[3] for m in msg.to(phone, "text")]


async def test_slip_offers_tax_invoice_only_when_merchant_can_issue(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    assert any("Reply TAX" in t for t in texts(msg))


async def test_no_offer_when_not_vat_registered(env):
    svc, repo, msg, gw, tag = env
    repo.invoice_cfg[T]["vat_registered"] = False
    await pay(svc, repo, msg, gw, tag)
    assert not any("Reply TAX" in t for t in texts(msg))
    assert await svc.handle_text(T, CUST, "TAX Acme 4123456789")          # told why, not silently ignored
    assert "can't issue tax invoices" in texts(msg)[-1] and not docs(msg)


async def test_missing_address_blocks_issuing(env):
    svc, repo, msg, gw, tag = env
    repo.invoice_cfg[T]["registered_address"] = ""
    await pay(svc, repo, msg, gw, tag)
    assert not svc.tax.can_issue(T)


async def test_two_step_conversation_issues_a_pdf(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    assert await svc.handle_text(T, CUST, "TAX")
    assert texts(msg)[-1] == "Send your company name and VAT number for a tax invoice."
    assert await svc.handle_text(T, CUST, "Acme Trading (Pty) Ltd, VAT 4123456789")
    (kind, _, _, (fname, caption, data)), = docs(msg)
    assert fname == "Tax-Invoice-TI-000001.pdf" and data[:4] == b"%PDF" and "TI-000001" in caption
    inv = repo.tax_invoices[0]
    assert (inv["bill_cents"], inv["vat_cents"], inv["buyer_vat"]) == (50000, 6522, "4123456789")
    assert inv["payment_id"] == p["id"] and inv["supplier"]["vat"] == "4999999999"
    assert repo.tax_requests[0]["status"] == "done"
    assert not await svc.handle_text(T, CUST, "what time do you open")      # request closed: normal routing again


async def test_one_message_form_and_asking_again_returns_same_invoice(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "tax invoice Acme Trading 4123456789")
    await svc.handle_text(T, CUST, "TAX")
    assert len(repo.tax_invoices) == 1 and len(docs(msg)) == 2
    assert "again" in docs(msg)[1][3][1]


async def test_bad_vat_keeps_request_open_for_a_retry(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "TAX")
    await svc.handle_text(T, CUST, "Acme 3123456789")
    assert "valid VAT number" in texts(msg)[-1] or "VAT number" in texts(msg)[-1]
    assert not repo.tax_invoices
    await svc.handle_text(T, CUST, "Acme 4123456789")
    assert len(repo.tax_invoices) == 1


async def test_name_only_asks_for_vat(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "TAX")
    await svc.handle_text(T, CUST, "Acme Trading")
    assert "VAT number" in texts(msg)[-1] and not repo.tax_invoices


async def test_cancel_closes_request(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "TAX")
    assert await svc.handle_text(T, CUST, "no thanks")
    assert repo.tax_requests[0]["status"] == "cancelled" and not repo.tax_invoices


async def test_unrelated_chatter_is_not_captured(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "TAX")
    assert not await svc.handle_text(T, CUST, "Can I move my lesson to Friday? Is the 9am slot free?")
    assert repo.tax_requests[0]["status"] == "awaiting"


async def test_expired_request_is_ignored(env):
    from datetime import timedelta
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    await svc.handle_text(T, CUST, "TAX")
    svc.cfg.clock.t += timedelta(minutes=31)
    assert not await svc.handle_text(T, CUST, "Acme 4123456789")
    assert not repo.tax_invoices


async def test_stranger_who_never_paid_is_not_hijacked(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    assert not await svc.handle_text(T, "+27 83 000 0000", "TAX invoice for the Smith job please")


async def test_only_after_a_real_payment_not_a_test(env):
    svc, repo, msg, gw, tag = env
    assert not await svc.handle_text(T, CUST, "TAX")


async def test_numbers_are_sequential_per_tenant(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    p1 = await pay(svc, repo, msg, gw, tag)
    a, _ = svc.tax.issue(p1["id"], "Acme", "4123456789")
    # second payment, different customer
    for b in repo.bills.values():
        b["status"] = "paid"
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=20000, description="Hire", staff_id="m1", created_by="m1")
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    other = "+27 71 222 3333"
    await svc.handle_text(T, other, f"PAY {tok}")
    sid = [k for k, v in repo.sessions.items() if v["payer_hash"] == svc.phone_hash(other)][0]
    await svc.handle_interactive(T, other, f"kb:tip:{sid}:0")
    await svc.handle_interactive(T, other, f"kb:pay:{sid}")
    gw.itn = {"reference": "kb-" + sid, "paid": True, "amount_cents": 20000, "pf_payment_id": "P2", "fee_cents": 0}
    await svc.confirm_payment(T, {}, b"", {})
    p2 = [p for p in repo.payments.values() if p["id"] != p1["id"]][0]
    b, _ = svc.tax.issue(p2["id"], "Beta", "4222222222")
    assert (a["number"], b["number"]) == (1, 2)


async def test_race_for_same_number_retries(env, monkeypatch):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    real = repo.insert_tax_invoice
    calls = {"n": 0}

    def flaky(row):
        calls["n"] += 1
        if calls["n"] == 1:                      # someone else grabs number 1 for a different payment
            real({**row, "payment_id": "other-payment"})
        return real(row)

    monkeypatch.setattr(repo, "insert_tax_invoice", flaky)
    inv, created = svc.tax.issue(p["id"], "Acme", "4123456789")
    assert created and inv["number"] == 2


async def test_tip_is_excluded_and_disclosed(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    inv, _ = svc.tax.issue(p["id"], "Acme", "4123456789")
    d = svc.tax.invoice_dict(inv, "", 5000, "ABCD1234")
    assert d["total_cents"] == 50000 and d["subtotal_cents"] + d["vat_cents"] == 50000
    assert "gratuity" in d["notes"] and "R 50.00" in d["notes"] and d["customer_vat"] == "4123456789"


async def test_test_payment_cannot_be_invoiced(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    for b in repo.bills.values():
        b["is_test"] = True
    from vula.tap.tax import TaxError
    with pytest.raises(TaxError):
        svc.tax.issue(p["id"], "Acme", "4123456789")


async def test_tenant_isolation_in_repo(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    svc.tax.issue(p["id"], "Acme", "4123456789")
    assert repo.get_tax_invoice("tenant-b", p["id"]) is None


@pytest.mark.parametrize("name", ["", "A", "   "])
async def test_short_name_rejected(env, name):
    from vula.tap.tax import TaxError
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    with pytest.raises(TaxError):
        svc.tax.issue(p["id"], name, "4123456789")


# ── PDF ──────────────────────────────────────────────────────────────────────────────────────
async def test_pdf_contains_the_legal_fields(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    inv, _ = svc.tax.issue(p["id"], "Acme Trading (Pty) Ltd", "4123456789", "5 Main Rd")
    pdf = svc.tax.render_pdf(inv)
    assert pdf[:4] == b"%PDF"
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf); f.flush()
        try:
            txt = subprocess.run(["pdftotext", f.name, "-"], capture_output=True, text=True).stdout
        except FileNotFoundError:
            pytest.skip("pdftotext not installed")
    for needle in ("Tax Invoice", "TI-000001", "Surf Shack", "4999999999", "Acme Trading", "4123456789",
                   "VAT No", "434.78", "65.22", "500.00"):
        assert needle.lower() in txt.lower(), needle


async def test_issued_details_do_not_drift_when_settings_change(env):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    inv, _ = svc.tax.issue(p["id"], "Acme", "4123456789")
    repo.invoice_cfg[T]["vat_number"] = "4000000000"
    repo.invoice_cfg[T]["company_name"] = "Renamed Co"
    d = svc.tax.render_pdf(inv)
    assert d[:4] == b"%PDF" and inv["supplier"]["vat"] == "4999999999"


# ── receipt-page endpoints ───────────────────────────────────────────────────────────────────
@pytest.fixture
def client(env):
    return TestClient(app)


async def test_receipt_json_flags_and_endpoints(env, client):
    pytest.importorskip("weasyprint")
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    tok = rc.make_token("rs", p["id"], 0)
    j = client.get(f"/v1/tap/receipt/{tok}").json()
    assert j["can_tax_invoice"] is True and j["tax_invoice_number"] is None
    assert client.get(f"/v1/tap/receipt/{tok}/tax-invoice.pdf").status_code == 404      # not requested yet
    r = client.post(f"/v1/tap/receipt/{tok}/tax-invoice", json={"company": "Acme", "vat": "4123456789"})
    assert r.status_code == 200 and r.json()["number"] == "TI-000001"
    r2 = client.post(f"/v1/tap/receipt/{tok}/tax-invoice", json={"company": "Other", "vat": "4222222222"})
    assert r2.json()["number"] == "TI-000001" and r2.json()["created"] is False        # fixed once issued
    assert repo.tax_invoices[0]["buyer_name"] == "Acme"
    pdf = client.get(f"/v1/tap/receipt/{tok}/tax-invoice.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and pdf.headers["cache-control"] == "no-store"
    assert client.get(f"/v1/tap/receipt/{tok}").json()["tax_invoice_number"] == "TI-000001"


async def test_endpoints_reject_bad_tokens_and_bad_vat(env, client):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    assert client.post("/v1/tap/receipt/nope/tax-invoice", json={"company": "Acme", "vat": "4123456789"}).status_code == 404
    assert client.get("/v1/tap/receipt/nope/tax-invoice.pdf").status_code == 404
    tok = rc.make_token("rs", p["id"], 0)
    r = client.post(f"/v1/tap/receipt/{tok}/tax-invoice", json={"company": "Acme", "vat": "1234"})
    assert r.status_code == 422
    r = client.post(f"/v1/tap/receipt/{tok}/tax-invoice", json={"company": "Acme", "vat": "3123456789"})
    assert r.status_code == 422 and "VAT number" in r.json()["detail"]
    repo.revoke_receipt(T, p["id"])
    assert client.post(f"/v1/tap/receipt/{tok}/tax-invoice", json={"company": "Acme", "vat": "4123456789"}).status_code == 404


async def test_receipt_hides_option_when_not_registered(env, client):
    svc, repo, msg, gw, tag = env
    repo.invoice_cfg[T]["vat_registered"] = False
    p = await pay(svc, repo, msg, gw, tag)
    j = client.get(f"/v1/tap/receipt/{rc.make_token('rs', p['id'], 0)}").json()
    assert j["can_tax_invoice"] is False
