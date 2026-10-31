"""Order defects from the 31 Oct takeoff review: PO numbers, suppliers per business, RFQ sender."""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from config import settings
from vula.api.server import app
from vula.takeoff import order_manager as om
from vula.takeoff.boq_generator import BOQ, BOQItem

client = TestClient(app, headers={"X-API-Key": settings.api_key or ""})


def _boq(name="Job"):
    return BOQ(project_name=name, tenant_id="t1", items=[
        BOQItem("A1", "flooring", "Carpet", "m²", 10, 1, 2, 1.5, 0, "s", 100, supplier_key="carpet_commercial")])


def test_po_numbers_never_repeat_across_jobs(tmp_path):
    db = om.SupplierDatabase(tmp_path / "o.db")
    with patch.object(om, "SupplierDatabase", return_value=db):
        a = om.OrderManager(_boq("A"), tenant_id="t1", job_id="job-aaa111").build_orders()
        b = om.OrderManager(_boq("B"), tenant_id="t1", job_id="job-bbb222").build_orders()
    pa = {o.po_number for o in a.orders}
    pb = {o.po_number for o in b.orders}
    assert pa and pb and not pa & pb          # both used to be PO-YYYYMM-001


def test_editing_a_default_supplier_gives_that_business_its_own_copy(tmp_path):
    db = om.SupplierDatabase(tmp_path / "s.db")
    base = next(s for s in db.get_all("tenant-a") if s.id == "sup_dw")
    base.discount_pct = 22.0
    db.upsert_supplier(base, "tenant-a")
    a = {s.name: s for s in db.get_all("tenant-a")}
    b = {s.name: s for s in db.get_all("tenant-b")}
    assert a["Drywall Specialist"].discount_pct == 22.0
    assert b["Drywall Specialist"].discount_pct == 5          # the default, untouched
    assert sum(1 for s in db.get_all("tenant-a") if s.name == "Drywall Specialist") == 1


def test_supplier_routes_need_a_business():
    assert client.get("/takeoff/suppliers/list").status_code == 400
    assert client.put("/takeoff/suppliers/sup_dw", json={"discount_pct": 50}).status_code == 400


def test_rfqs_are_signed_by_the_business_not_one_client():
    src = inspect.getsource(om)
    for literal in ("DIGG", "Judy", "PAT44740093", "digg.co.za"):
        assert literal not in src
    po = MagicMock(project_name="P", po_number="PO-1", estimated_total=1000.0, lines=[],
                   supplier=MagicMock(contact_name="Sam", name="S", email="s@x.co", category="flooring_supplier"))
    mgr = om.OrderManager.__new__(om.OrderManager)
    mgr.tenant_id = "t1"
    with patch("vula.commerce.boq_template.brand", return_value={"display_name": "Acme Interiors"}), \
         patch.object(om._settings, "resend_api_key", "k"), \
         patch("httpx.AsyncClient") as AC:
        AC.return_value.__aenter__.return_value.post = AsyncMock(return_value=MagicMock(raise_for_status=lambda: None))
        assert asyncio.run(mgr._send_rfq_email(po)) is True
        sent = AC.return_value.__aenter__.return_value.post.await_args.kwargs["json"]
    assert "Acme Interiors" in sent["from"] and "Acme Interiors" in sent["text"]


def test_sending_rfqs_for_real_is_refused_not_faked():
    from vula.takeoff import api
    api._jobs["rfq1"] = {"status": "complete", "tenant_id": "t1", "orders": {"count": 2}}
    r = client.post("/takeoff/rfq1/send?tenant_id=t1", json={"dry_run": False})
    assert r.status_code == 501
    r = client.post("/takeoff/rfq1/send?tenant_id=t1", json={"dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True
