"""A project's documents on the client's link: view, ask, download (3 Nov 2026).

Ian: "We want to allow the client to view, but they can request if they want to download."
Made-up business, project, suppliers and figures throughout.
"""
import io
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.commerce import project_share_docs as sd

T = "thandi-demo"
TOKEN = "tok-123"


def _pdf_bytes(pages=2):
    import pymupdf
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Invoice page {i + 1}")
    return doc.tobytes()


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, "PNG")
    return buf.getvalue()


FILES = {"https://f/inv": _pdf_bytes(2), "https://f/own": _pdf_bytes(1), "https://f/quote": _png_bytes(), "https://f/st": _pdf_bytes(1),
         "https://f/other": _pdf_bytes(1), "https://f/xlsx": b"PK-not-viewable"}


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.row = db, name, [], None, None

    def select(self, *a): return self
    def limit(self, *a): return self
    def order(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def insert(self, row): self.row = row; return self
    def update(self, patch): self.patch = patch; return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.row is not None:
            r = {"id": f"req-{len(rows) + 1}", "requested_at": datetime.now(timezone.utc).isoformat(), **self.row}
            rows.append(r)
            return SimpleNamespace(data=[dict(r)])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in hit])


class _DB:
    def __init__(self, **t): self.tables = t
    def table(self, name): return _Q(self, name)


def _doc(i, category, supplier, number, total, url, project="Seaview", tenant=T, ext=".pdf", supplier_id=None):
    return {"id": i, "tenant_id": tenant, "project": project, "category": category, "status": "filed",
            "supplier_id": supplier_id, "filename": f"{i}{ext}", "file_url": url, "mime": "",
            "created_at": "2026-10-01T08:00:00",
            "fields": {"supplier": supplier, "document_number": number, "total_cents": total, "date": "2026-10-01"}}


@pytest.fixture
def world(monkeypatch):
    db = _DB(
        commerce_suppliers=[{"id": "s1", "tenant_id": T, "name": "Tile Co", "aliases": []}],
        vula_filed_documents=[
            _doc("inv", "Invoice", "Tile Co", "IN-1", 300000, "https://f/inv"),
            _doc("quote", "Quote / Estimate", "Tile Co", "Q-1", 450000, "https://f/quote", ext=".png"),
            _doc("st", "Account Statement", "Tile Co", None, None, "https://f/st"),       # never on the link
            _doc("xls", "Bill of Quantities (BOQ)", None, None, None, "https://f/xlsx", ext=".xlsx"),
            _doc("other", "Invoice", "Tile Co", "IN-9", 100, "https://f/other", project="Main Road"),
            _doc("oth-t", "Invoice", "Tile Co", "IN-8", 100, "https://f/other", tenant="other"),
            _doc("own", "Invoice", "Thandi Builders", "TB-INV-1", 500000, "https://f/own"),
        ],
        vula_project_share_requests=[])
    from vula.commerce import project_file, project_tracker as pt, service, boq_online
    import vula.commerce.project_share_docs as sdm
    import vula.api.commerce as api_commerce
    from vula.api import project_track
    from vula import storage_links
    monkeypatch.setattr(project_file, "_client", lambda: db)
    monkeypatch.setattr(sdm, "_client", lambda: db)
    monkeypatch.setattr(service, "canonical_project", lambda tid, n: n)
    share = {"id": "share-1", "tenant_id": T, "project": "Seaview", "view": "price", "views": 0}
    state = {"share": share, "asked": []}
    monkeypatch.setattr(pt, "share_for_token", lambda tok: state["share"] if tok == TOKEN else None)
    monkeypatch.setattr(pt, "position", lambda tid, p: {"project": p, "as_of": "2026-11-03"})
    monkeypatch.setattr(pt, "note_view", lambda s: None)
    monkeypatch.setattr(boq_online, "load", lambda tid, p: {"lines": []})

    async def brand(tid):
        return {"name": "Thandi Builders", "accent_color": "#335544"}
    monkeypatch.setattr(api_commerce, "public_brand", brand)
    fetch = lambda url: FILES[url]  # noqa: E731
    monkeypatch.setattr(project_track, "_fetch", lambda: fetch)
    monkeypatch.setattr(storage_links, "fetch_sync", fetch)

    async def ask_owner(share_, request_id, text):
        state["asked"].append((request_id, text))
    monkeypatch.setattr(project_track, "_ask_owner", ask_owner)
    app = FastAPI()
    app.include_router(project_track.router)
    state["client"] = TestClient(app)
    state["db"] = db
    return state


def test_the_link_lists_the_client_documents_view_only(world):
    page = world["client"].get(f"/track/{TOKEN}").text
    assert "Documents (4)" in page                                   # invoices, quote, BOQ — no statement
    assert "(our documents)" not in page and "<div class='who'>Thandi Builders</div>" in page   # the business's own invoice
    assert f"/track/{TOKEN}/doc/inv" in page and f"/track/{TOKEN}/doc/quote" in page
    assert f"/track/{TOKEN}/doc/xls'" not in page                    # a spreadsheet can't be shown — request only
    assert "IN-9" not in page and "IN-8" not in page                 # another project's, another business's
    assert "Request to download all" in page and "/download/" not in page


def test_a_document_is_shown_as_watermarked_pictures(world):
    c = world["client"]
    view = c.get(f"/track/{TOKEN}/doc/inv").text
    assert view.count(f"/track/{TOKEN}/doc/inv/") == 2 and "Request download" in view
    r = c.get(f"/track/{TOKEN}/doc/inv/1.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store" and r.content[:4] == b"\x89PNG"
    assert c.get(f"/track/{TOKEN}/doc/inv/3.png").status_code == 404
    assert c.get(f"/track/{TOKEN}/doc/quote/1.png").status_code == 200
    for bad in ("st", "other", "oth-t", "nope"):
        assert c.get(f"/track/{TOKEN}/doc/{bad}").status_code == 404
        assert c.get(f"/track/{TOKEN}/doc/{bad}/1.png").status_code == 404


def test_no_download_without_the_business_saying_yes(world):
    c = world["client"]
    assert c.get(f"/track/{TOKEN}/download/inv").status_code == 403
    assert c.get(f"/track/{TOKEN}/download.zip").status_code == 403
    assert c.get("/track/wrong/doc/inv").status_code == 404


def test_a_request_asks_the_owner_once(world):
    c = world["client"]
    r = c.post(f"/track/{TOKEN}/request", data={"scope": "inv"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?asked=1")
    c.post(f"/track/{TOKEN}/request", data={"scope": "inv"}, follow_redirects=False)
    rows = world["db"].tables["vula_project_share_requests"]
    assert len(rows) == 1 and rows[0]["scope"] == "inv" and rows[0]["status"] == "requested"
    assert len(world["asked"]) == 1 and "Tile Co · IN-1" in world["asked"][0][1]
    page = c.get(f"/track/{TOKEN}?asked=1").text
    assert "gone to the team" in page and "download requested" in page
    for bad in ("st", "other", "oth-t"):
        assert c.post(f"/track/{TOKEN}/request", data={"scope": bad}).status_code == 404


def test_after_a_yes_the_file_downloads_and_only_for_a_while(world):
    c = world["client"]
    c.post(f"/track/{TOKEN}/request", data={"scope": "inv"}, follow_redirects=False)
    req = world["db"].tables["vula_project_share_requests"][0]
    sd.decide(T, req["id"], True, by="27820000001")
    r = c.get(f"/track/{TOKEN}/download/inv")
    assert r.status_code == 200 and r.content == FILES["https://f/inv"]
    assert c.get(f"/track/{TOKEN}/download/quote").status_code == 403          # only what was approved
    assert c.get(f"/track/{TOKEN}/download.zip").status_code == 403
    req["approved_until"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert c.get(f"/track/{TOKEN}/download/inv").status_code == 403            # expired


def test_a_yes_for_all_opens_the_client_copy_zip(world):
    c = world["client"]
    c.post(f"/track/{TOKEN}/request", data={"scope": "all"}, follow_redirects=False)
    req = world["db"].tables["vula_project_share_requests"][0]
    with pytest.raises(RuntimeError):
        sd.decide("other", req["id"], True)                                    # another business can't answer
    sd.decide(T, req["id"], True)
    r = c.get(f"/track/{TOKEN}/download.zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any("/01 Invoices/Tile Co/" in n for n in names)
    assert not any("Statements" in n for n in names)
    assert "Download all (zip)" in c.get(f"/track/{TOKEN}").text
    with pytest.raises(RuntimeError):
        sd.decide(T, req["id"], False)                                         # already answered


def test_a_declined_request_stays_view_only(world):
    c = world["client"]
    c.post(f"/track/{TOKEN}/request", data={"scope": "inv"}, follow_redirects=False)
    sd.decide(T, world["db"].tables["vula_project_share_requests"][0]["id"], False)
    assert c.get(f"/track/{TOKEN}/download/inv").status_code == 403


def test_the_owner_answers_on_whatsapp(world, monkeypatch):
    import asyncio
    from vula.api import whatsapp as wa
    from vula import open_questions as oq
    world["client"].post(f"/track/{TOKEN}/request", data={"scope": "inv"}, follow_redirects=False)
    req = world["db"].tables["vula_project_share_requests"][0]
    sent = []

    async def send(to, text, **k):
        sent.append(text)
    monkeypatch.setattr(wa, "_send_reply", send)
    monkeypatch.setattr(oq, "close_for", lambda *a, **k: None)
    q = {"ref_id": req["id"], "kind": "doc_download"}
    assert asyncio.run(wa._answer_doc_download(T, "27820000001", "what's this?", q)) is False
    assert asyncio.run(wa._answer_doc_download(T, "27820000001", "yes", q)) is True
    assert req["status"] == "approved" and "downloads are open" in sent[-1]


def test_open_requests_are_capped_per_link(world):
    for i in range(sd.MAX_OPEN):
        world["db"].tables["vula_project_share_requests"].append(
            {"id": f"x{i}", "tenant_id": T, "share_id": "share-1", "project": "Seaview", "scope": f"s{i}",
             "status": "requested"})
    with pytest.raises(ValueError):
        sd.ask(world["share"], "inv")
