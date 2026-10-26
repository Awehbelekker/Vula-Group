"""The business's own documents are never supplier bills (2026-10-26: DIGG had four supplier
records for itself and 21 of its own invoices booked as bills), and bank details must name a
real bank."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import service
from vula.commerce.extraction_quality import is_sa_bank, verify_identifiers

OWN = ["digg-demo", "DIGG Architecture", "Aweh Be Lekker (Pty) Ltd t/a DIGG", "Aweh Be Lekker (Pty) Ltd", "DIGG"]


@pytest.mark.parametrize("name,own", [
    ("DIGG", True), ("DIGG Architects", True), ("AwehBelekker (Pty) Ltd t/a DIGG", True),
    ("Digg Demo", True), ("Diggers Supplies", False), ("SOLID CAPE (PTY) LTD", False),
    ("Design by Sorelle PTY LTD", False), ("", False)])
def test_every_name_the_business_goes_by(name, own):
    assert service.is_own_name(name, OWN) is own


def test_its_own_vat_number_is_itself_whatever_the_name():
    assert service.is_own_name("Some Name", OWN, ["4760310369"], tax_id="4760 310 369")


@pytest.mark.asyncio
async def test_own_names_come_from_settings_split_at_t_a(monkeypatch):
    service._OWN_CACHE.clear()
    monkeypatch.setattr(service, "get_invoice_settings",
                        AsyncMock(return_value={"trading_as": "Aweh Be Lekker (Pty) Ltd t/a DIGG",
                                                "vat_number": "4760310369"}))
    with patch("vula.api.tenants.get_config", return_value={"display_name": "DIGG Architecture"}):
        names, numbers = await service.own_business_names("digg-demo")
    assert {"DIGG", "Aweh Be Lekker (Pty) Ltd", "DIGG Architecture", "digg-demo"} <= set(names)
    assert numbers == ["4760310369"]
    service._OWN_CACHE.clear()


def test_classify_direction_uses_every_own_name():
    assert service.classify_direction("DIGG", "DIGG Architecture", "digg-demo",
                                      supplier_known=True, own_names=OWN)[0] == "outbound"
    assert service.classify_direction("Solid Cape", "DIGG Architecture", "digg-demo",
                                      supplier_known=True, own_names=OWN)[:2] == ("inbound", True)


@pytest.mark.parametrize("bank,ok", [("FNB", True), ("First National Bank (FNB)", True), ("Absa", True),
                                     ("NEDBANK", True), ("Capitec BUSINESS", True), ("Standard Bank", True),
                                     ("City of Cape Town (listed beneficiary)", False), ("", False)])
def test_recognised_banks(bank, ok):
    assert is_sa_bank(bank) is ok


def test_bank_details_at_no_recognised_bank_are_dropped():
    page = "Pay to: City of Cape Town (listed beneficiary) 1234561913"
    f = verify_identifiers({"supplier_bank": {"bank_name": "City of Cape Town (listed beneficiary)",
                                              "account_number": "1234561913"}}, page)
    assert "supplier_bank" not in f
    assert {"field": "supplier_bank", "why": "not a recognised bank"} in f["_dropped_identifiers"]


def test_supplier_records_never_take_bank_details_at_no_recognised_bank():
    from vula.commerce import supplier_details as sd
    up, change = sd.updates_from_document(
        {"id": "s1", "bank_account_number": "62009999999"},
        {"supplier_bank": {"bank_name": "City of Cape Town (listed beneficiary)", "account_number": "1234561913"}})
    assert up == {} and change is None
