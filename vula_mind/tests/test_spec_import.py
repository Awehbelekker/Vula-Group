"""Spec sheets from a catalogue PDF (vula/commerce/spec_import.py, migration 311, 25 Nov 2026): the
text is read in code; what the model lists is kept only when it is printed on those pages; prices
are never read; a value whose unit doesn't fit its label is flagged; products are matched by name;
nothing changes until `apply`, which writes through clean_specs and reads each back."""
import asyncio

import pytest

from tests.fake_supabase import FakeSupabase
from vula.commerce import service, spec_import as si


def _pdf(pages):
    import pymupdf
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        y = 60
        for line in text.splitlines():
            page.insert_text((50, y), line, fontsize=10)
            y += 14
    return doc.tobytes()


# Scrambled like a real catalogue: labels and values apart.
PAGE_1 = """Stick down Luxury Vinyl Planks
ROCOCO XL
Total Thickness
Product
2.5mm
0.5mm + AO9
Wear Layer
+/- 4490gsm
Total Weight
Heavy Commercial
Location Grade
228.6mm x 1524mm
Plank Size
Warranties
12 years - Heavy Commercial
CARDAMOM
SAFFRON GOLD
R 459.00 per m2"""
PAGE_2 = """TARASAFE ULTRA
Slip resistance R10
Width 2 m
Phthalate free"""


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_products": [
            {"id": "p1", "tenant_id": "acme", "name": "Rococo XL", "category": "lvt", "status": "active", "price_cents": 1},
            {"id": "p2", "tenant_id": "acme", "name": "Tarasafe Ultra 2m", "category": "safety", "status": "active", "price_cents": 1},
            {"id": "p3", "tenant_id": "acme", "name": "Tarasafe Ultra 1.5m", "category": "safety", "status": "active", "price_cents": 1},
            {"id": "x1", "tenant_id": "other", "name": "Rococo XL", "status": "active", "price_cents": 1},
        ],
        "commerce_spec_imports": [], "vula_merchant_audit": [],
    })
    monkeypatch.setattr(service, "_client", lambda: d)
    return d


MODEL_SAYS = [
    {"name": "ROCOCO XL", "grade": "Heavy Commercial", "benefits": ["Self cleaning surface"],
     "specs": [["Total Thickness", "2.5mm"], ["Wear Layer", "0.5mm + AO9"], ["Total Weight", "+/- 4490gsm"],
               ["Plank Size", "228.6mm x 1524mm"], ["Warranties", "12 years - Heavy Commercial"],
               ["Price", "R 459.00 per m2"], ["Total Thickness", "3mm"], ["Width", "4 m"]],
     "colours": ["Cardamom", "Saffron Gold", "Midnight Blue"]},
    {"name": "Tarasafe Ultra", "specs": [["Slip resistance", "R10"], ["Width", "2 m"]], "benefits": ["Phthalate free"]},
    {"name": "Invented Range", "specs": [["Width", "2 m"]]},
]


def test_only_what_is_printed_is_kept(db):
    page = si._Page(PAGE_1)
    g = si.grounded(MODEL_SAYS[0], page)
    assert g["ok"] and g["name"] == "ROCOCO XL" and g["grade"] == "Heavy Commercial"
    rows = dict(g["rows"])
    assert rows["Total Thickness"] == "2.5mm" and rows["Wear Layer"] == "0.5mm + AO9"
    assert rows["Plank Size"] == "228.6mm x 1524mm" and rows["Colours"] == "Cardamom, Saffron Gold"
    assert "Price" not in rows and "Width" not in rows                 # never a price; 4 m isn't printed there
    assert g["benefits"] == []                                         # "Self cleaning surface" isn't printed
    aside = " ".join(g["aside"])
    assert "prices aren't read" in aside and "Midnight Blue" in aside and "Self cleaning" in aside
    assert si.grounded(MODEL_SAYS[2], si._Page(PAGE_2))["ok"] is False


def test_price_and_units():
    assert si.is_price("Price", "459") and si.is_price("Size", "R 1 299.00") and not si.is_price("Slip resistance", "R10")
    assert si.unit_doubt("Total Thickness", "0.5mm") == ""
    assert "doesn't look like" in si.unit_doubt("Total Thickness", "Heavy Commercial")
    assert si.unit_doubt("Warranties", "12 years") == "" and si.unit_doubt("Backing", "Felt") == ""


def test_matching_by_name(db):
    prods = service._client().table("commerce_products").select("*").eq("tenant_id", "acme").execute().data
    assert si.match("ROCOCO XL", prods)["product_id"] == "p1"
    m = si.match("Tarasafe Ultra", prods)                               # two of yours hold that name: you pick
    assert m["product_id"] is None and {c["id"] for c in m["candidates"]} == {"p2", "p3"}
    assert si.match("Nothing like it", prods)["product_id"] is None


def test_the_whole_read_then_apply(db):
    pdf = _pdf([PAGE_1, "", PAGE_2])
    row = si.start("acme", pdf, "catalogue.pdf", {"email": "o@acme"})
    assert row["pages"] == 3 and row["status"] == "reading"
    asked = []

    async def fake(text):
        asked.append(text)
        return MODEL_SAYS
    asyncio.run(si.run("acme", row["id"], row["_pages"], ask=fake))
    got = si.get("acme", row["id"])
    assert got["status"] == "ready" and got["pages_read"] == 3 and got["result"]["skipped_pages"] == [2]
    items = {i["key"]: i for i in got["result"]["items"]}
    assert items["rococoxl"]["product_id"] == "p1"
    taras = items["tarasafeultra"]
    assert taras["product_id"] is None and len(taras["candidates"]) == 2
    with pytest.raises(si.ImportError_):
        asyncio.run(si.apply("other", row["id"], []))                    # another business's read isn't there

    out = asyncio.run(si.apply("acme", row["id"], [
        {"key": "rococoxl", "product_id": "p1"},
        {"key": "tarasafeultra", "product_id": "x1"},                   # not this business's product
        {"key": "tarasafeultra", "create": {"category": "safety"}},
        {"key": "invented"},
    ], actor={"email": "o@acme"}))
    assert [d["product_id"] for d in out["done"]][0] == "p1" and out["done"][1]["created"]
    assert {r["why"] for r in out["refused"]} >= {"Pick one of your products for it.", "Not in this catalogue read."}
    p1 = service._client().table("commerce_products").select("*").eq("id", "p1").execute().data[0]
    assert dict(p1["specs"]["rows"])["Wear Layer"] == "0.5mm + AO9"
    new = service._client().table("commerce_products").select("*").eq("id", out["done"][1]["product_id"]).execute().data[0]
    assert new["status"] == "draft" and new["price_cents"] == 0 and new["in_stock"] is False
    assert si.get("acme", row["id"])["applied"] == ["rococoxl", "tarasafeultra"]


def test_refuses_what_isnt_a_catalogue(db):
    with pytest.raises(si.ImportError_):
        si.start("acme", b"hello", "x.pdf")
    with pytest.raises(si.ImportError_):
        si.start("acme", b"", "x.pdf")


def test_a_scanned_pdf_says_so(db):
    row = si.start("acme", _pdf(["", ""]), "scan.pdf")

    async def never(text):
        raise AssertionError("nothing to ask")
    asyncio.run(si.run("acme", row["id"], row["_pages"], ask=never))
    got = si.get("acme", row["id"])
    assert got["status"] == "failed" and "scanned" in got["error"]


def test_allowance_used_up_stops_and_says_why(db):
    row = si.start("acme", _pdf([PAGE_1]), "c.pdf")

    class SpendCapReached(Exception):
        pass

    async def capped(text):
        raise SpendCapReached("acme")
    asyncio.run(si.run("acme", row["id"], row["_pages"], ask=capped))
    got = si.get("acme", row["id"])
    assert got["status"] == "failed" and "allowance" in got["error"]
