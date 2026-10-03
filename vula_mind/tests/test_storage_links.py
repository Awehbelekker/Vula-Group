"""Tenant files are private; links are signed on the way out (2026-10-03, Ian).

The documents / evidence / signatures buckets were public — every filed bank statement, invoice
and signature opened for anyone holding its link. They're private now (migration 192): the
stored link is an identifier, signed whenever it's handed out and read through the Storage API.
"""
import pytest

from vula import storage_links as sl

HOST = "https://proj.supabase.co"
DOC = f"{HOST}/storage/v1/object/public/documents/off-the-hook/333c_account_statement_2026_07_14.pdf"
LOGO = f"{HOST}/storage/v1/object/public/product-images/digg-demo/logo.png"


class _Bucket:
    def __init__(self, log, name, fail=False):
        self.log, self.name, self.fail = log, name, fail

    def create_signed_url(self, path, ttl):
        self.log.append(("sign", self.name, path, ttl))
        if self.fail:
            raise RuntimeError("storage down")
        return f"{HOST}/storage/v1/object/sign/{self.name}/{path}?token=t{len(self.log)}"

    def create_signed_urls(self, paths, ttl):
        self.log.append(("batch", self.name, tuple(paths), ttl))
        return [{"path": p, "error": None,
                 "signedURL": f"{HOST}/storage/v1/object/sign/{self.name}/{p}?token=b"} for p in paths]

    def download(self, path):
        self.log.append(("download", self.name, path))
        return b"%PDF-bytes"


class _Client:
    def __init__(self, fail=False):
        self.log, self.fail = [], fail
        self.storage = self

    def from_(self, name):
        return _Bucket(self.log, name, self.fail)


@pytest.fixture()
def client(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "supabase_url", HOST)
    sl._cache.clear()
    c = _Client()
    monkeypatch.setattr(sl, "_client", lambda: c)
    return c


def test_only_our_private_buckets_are_recognised(client):
    assert sl.parse(DOC) == ("documents", "off-the-hook/333c_account_statement_2026_07_14.pdf")
    assert sl.parse(LOGO) is None                                         # stays public
    assert sl.parse("https://evil.example/storage/v1/object/public/documents/x.pdf") is None
    assert sl.parse(None) is None and sl.parse("") is None


def test_a_link_handed_out_is_signed_and_reused(client):
    a = sl.signed(DOC)
    b = sl.signed(DOC)
    assert "/object/sign/documents/" in a and a == b
    assert [e[0] for e in client.log] == ["sign"]                         # cached
    assert sl.signed(LOGO) == LOGO and len(client.log) == 1               # public untouched
    week = sl.signed(DOC, sl.SHARE_TTL)                                   # WhatsApp link: 7 days
    assert client.log[-1][3] == 7 * 24 * 3600 and week != a


def test_signing_failure_never_loses_the_link(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "supabase_url", HOST)
    sl._cache.clear()
    monkeypatch.setattr(sl, "_client", lambda: _Client(fail=True))
    assert sl.signed(DOC) == DOC


def test_a_signed_link_saved_back_becomes_the_identifier_again(client):
    signed = sl.signed(f"{HOST}/storage/v1/object/public/signatures/digg-demo/signature.png")
    assert sl.canonical(signed) == f"{HOST}/storage/v1/object/public/signatures/digg-demo/signature.png"
    assert sl.canonical(LOGO) == LOGO


def test_a_page_of_rows_is_signed_in_one_call(client):
    other = DOC.replace("333c_account", "444d_account")
    rows = [{"filename": "a.pdf", "file_url": DOC}, {"filename": "b", "file_url": None},
            {"filename": "c.pdf", "file_url": other}, {"filename": "logo", "file_url": LOGO}]
    sl.sign_row(rows)
    assert [e[0] for e in client.log] == ["batch"]                        # not one call per row
    assert "/object/sign/" in rows[0]["file_url"] and "/object/sign/" in rows[2]["file_url"]
    assert rows[1]["file_url"] is None and rows[3]["file_url"] == LOGO


@pytest.mark.asyncio
async def test_the_server_reads_private_files_through_the_storage_api(client):
    assert await sl.fetch(DOC) == b"%PDF-bytes"
    assert sl.fetch_sync(sl.signed(DOC)) == b"%PDF-bytes"                # a signed link too
    assert [e[0] for e in client.log] == ["download", "sign", "download"]


@pytest.mark.asyncio
async def test_saved_signature_keeps_the_identifier(client, monkeypatch):
    from vula.commerce import service
    saved = {}

    class _Q:
        def __getattr__(self, name):
            return lambda *a, **k: self

        def insert(self, payload, **_k):
            saved.update(payload)
            return self

        update = insert

        def limit(self, *_a):
            return self

        def execute(self):
            return type("R", (), {"data": [dict(saved)] if saved else []})()

    monkeypatch.setattr(service, "_client", lambda: type("C", (), {"table": lambda s, n: _Q()})())
    shown = sl.signed(f"{HOST}/storage/v1/object/public/signatures/digg-demo/signature/1-sig.png")
    await service.upsert_invoice_settings("digg-demo", {"signature_url": shown})
    assert saved["signature_url"] == f"{HOST}/storage/v1/object/public/signatures/digg-demo/signature/1-sig.png"
