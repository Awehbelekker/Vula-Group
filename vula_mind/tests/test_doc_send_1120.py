"""20 Nov 2026 (Ian, DIGG): "it would be nice if Vula can send this [the bank letters] to a client when
asked." vula/commerce/doc_send.py — find the document, preview, then send by email or WhatsApp.
Made-up business and figures."""
import pytest

from vula.commerce import company_docs, doc_audit, doc_send, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _doc(i, category, filename, summary, account=None, **kw):
    fields = dict(kw.pop("fields", {}))
    if account:
        fields["account_number"] = account
    return {"id": i, "tenant_id": kw.pop("tenant_id", T), "category": category, "filename": filename,
            "summary": summary, "status": kw.pop("status", "filed"), "fields": fields, "file_url": f"u/{i}.pdf",
            "mime": "application/pdf", "created_at": kw.pop("created_at", "2026-11-20T08:08:00Z"),
            "content_hash": f"h-{i}", "supplier_id": None, "commerce_invoice_id": None, "project": None}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            _doc("b74", "Correspondence", "Correspondence 0808.pdf",
                 "A letter from First National Bank confirming the account details of Mandla Designs (Pty) Ltd. "
                 "Information on how to validate the letter.", account="62000006574"),
            _doc("b76", "Compliance Certificate", "Bank letter 7663.pdf",
                 "Bank confirmation letter from FNB for Mandla Designs (Pty) Ltd", account="62000007663"),
            _doc("b74old", "Correspondence", "old.pdf",
                 "An account confirmation letter from FNB for Mandla Designs", account="62000006574",
                 created_at="2026-09-29T08:00:00Z"),
            _doc("sup", "Correspondence", "Steel bank letter.pdf",
                 "Bank confirmation letter from FNB for Steel Supplies (Pty) Ltd", account="62000001111"),
            _doc("tcs", "General Document", "Tax Clearance.pdf",
                 "Tax clearance certificate issued by SARS for Mandla Designs, with tax compliance status PIN"),
            _doc("stmt", "Bank Statement", "FNB statement.pdf", "FNB statement for Mandla Designs"),
            _doc("gone", "Tax / SARS", "Old tax clearance.pdf", "Tax clearance for Mandla Designs", status="duplicate"),
            _doc("x1", "Compliance Certificate", "Other bank letter.pdf", "Bank confirmation letter for Mandla Designs",
                 tenant_id="other")],
        "commerce_suppliers": [{"id": "s1", "tenant_id": T, "name": "Belekkie Skips", "aliases": ["R Engelbrecht"],
                                "contact_email": "skips@example.com", "contact_phone": "0821234567"}],
        "vula_merchant_audit": []})
    for mod in (doc_send, doc_audit, service, company_docs):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Mandla Designs (Pty) Ltd"])
    monkeypatch.setattr("vula.clients.resolve", lambda tid, q: {"none": True})
    sent = {"email": [], "resend": [], "wa": [], "template": []}

    async def fetch(url, timeout=60.0):
        return b"%PDF-1.4 " + url.encode()

    async def tenant_email(tid, to, subject, body, attachments=None):
        sent["email"].append((to, subject, attachments[0]["filename"], attachments[0]["content"][:4]))
        return sent.get("mailbox_ok", True)

    async def resend(to, subject, html, text, attachments=None):
        sent["resend"].append((to, subject))
        return True

    async def wa_doc(to, data, filename, caption="", tenant_id="", content_type="application/pdf"):
        sent["wa"].append((to, filename, caption))
        return to not in sent.get("wa_fail", set())

    async def template(tid, to, name, params, header_image_url=None, lang=None, header_document=None):
        sent["template"].append((to, name, params, header_document))
        return "wamid" if sent.get("template_ok") else None

    monkeypatch.setattr("vula.storage_links.fetch", fetch)
    monkeypatch.setattr("vula.storage_links.signed", lambda url, ttl=0: f"https://signed/{url}")
    monkeypatch.setattr("vula.commerce.mail_router.send_tenant_email", tenant_email)
    monkeypatch.setattr("vula.api.email._send", resend)
    monkeypatch.setattr("vula.api.whatsapp._send_invoice_document", wa_doc)
    monkeypatch.setattr("vula.api.whatsapp.send_template_message", template)
    monkeypatch.setattr("vula.consent.opted_out", lambda tid, phone: False)
    d.sent = sent
    return d


def test_a_request_to_send_a_document_is_recognised():
    assert doc_send.is_send_request("Please send our bank letter to Atlantis Foods")
    assert doc_send.is_send_request("email the tax clearance to accounts@atlantis.co.za")
    assert doc_send.is_send_request("forward the proof of payment for R5,600 to Belekkie Skips")
    assert not doc_send.is_send_request("what is our bank balance?")
    assert not doc_send.is_send_request("send me a quote for 20 m2 of tiling")


def test_bank_letters_are_one_per_account_ours_only_and_the_newest(db):
    docs = doc_send.find(T, "send our bank letter to Atlantis")["documents"]
    assert {d["id"] for d in docs} == {"b74", "b76"}          # newest per account; never the supplier's
    assert {d["document"] for d in docs} == {"bank confirmation letter (account …6574)",
                                             "bank confirmation letter (account …7663)"}
    assert [d["id"] for d in doc_send.find(T, "the bank letter for the 6574 account")["documents"]] == ["b74"]
    assert [d["id"] for d in doc_send.find(T, "tax clearance")["documents"]] == ["tcs"]   # never the duplicate


def test_the_recipient_is_found_or_asked_for(db):
    assert doc_send.recipient(T, "accounts@atlantis.co.za")["email"] == "accounts@atlantis.co.za"
    assert doc_send.recipient(T, "082 765 4321")["phone"] == "27827654321"
    sup = doc_send.recipient(T, "R Engelbrecht")
    assert sup["name"] == "Belekkie Skips" and sup["email"] == "skips@example.com" and sup["phone"] == "27821234567"
    assert doc_send.recipient(T, "Nobody Known")["none"]


@pytest.mark.asyncio
async def test_the_preview_sends_nothing_and_confirm_emails_the_file(db):
    p = await doc_send.send(T, "b74", "Belekkie Skips")
    assert p["preview"] and p["via"] == "email" and "account …6574" in p["message"]
    assert not db.sent["email"] and not db.sent["wa"]
    out = await doc_send.send(T, "b74", "Belekkie Skips", confirm=True, actor={"email": "judy@example.com"})
    assert out["sent"] and out["recorded"] and db.sent["email"][0][0] == "skips@example.com"
    assert db.sent["email"][0][2] == "Correspondence 0808.pdf" and db.sent["email"][0][3] == b"%PDF"
    assert "Bank confirmation letter" in db.sent["email"][0][1] and "Mandla Designs" in db.sent["email"][0][1]
    row = next(r for r in db.rows["vula_filed_documents"] if r["id"] == "b74")
    assert row["fields"]["sent"][0]["to"] == "skips@example.com" and row["fields"]["sent"][0]["by"] == "judy@example.com"
    assert any(a["action"] == "document_sent" for a in db.rows["vula_merchant_audit"])
    assert out["reply_verbatim"].startswith("Sent our bank confirmation letter (account …6574) to Belekkie Skips")


@pytest.mark.asyncio
async def test_no_mailbox_goes_through_vula_s_email(db):
    db.sent["mailbox_ok"] = False
    out = await doc_send.send(T, "tcs", "accounts@atlantis.co.za", confirm=True)
    assert out["sent"] and out["how"] == "Vula's email" and db.sent["resend"][0][0] == "accounts@atlantis.co.za"


@pytest.mark.asyncio
async def test_whatsapp_inside_and_outside_the_24_hours(db):
    out = await doc_send.send(T, "b76", "0827654321", confirm=True)
    assert out["sent"] and out["via"] == "WhatsApp" and db.sent["wa"][0][0] == "27827654321"
    db.sent["wa_fail"], db.sent["template_ok"] = {"27827654321", "27821234567"}, True   # outside 24 h: the template
    out = await doc_send.send(T, "b76", "0827654321", confirm=True)
    assert out["sent"] and out["how"] == "WhatsApp (message template)"
    to, name, params, header = db.sent["template"][-1]
    assert name == "vula_document_share" and header["link"] == "https://signed/u/b76.pdf"
    db.sent["template_ok"] = False                                    # no template: the email on file
    out = await doc_send.send(T, "b76", "Belekkie Skips", channel="whatsapp", confirm=True)
    assert out["sent"] and out["via"] == "email" and "WhatsApp couldn't take it" in out["reply_verbatim"]
    with pytest.raises(doc_send.Refused, match="nothing was sent"):   # nowhere to go
        await doc_send.send(T, "b76", "0827654321", confirm=True)
    out = await doc_send.send(T, "b76", "0827654321", confirm=True, requester_phone="27820000000")
    assert out["forward"] and db.sent["wa"][-1][0] == "27820000000"   # sent to the person asking, to forward


@pytest.mark.asyncio
async def test_refusals(db, monkeypatch):
    with pytest.raises(doc_send.Refused, match="owner or manager"):
        await doc_send.send(T, "stmt", "accounts@atlantis.co.za", confirm=True, full_access=False)
    with pytest.raises(doc_send.Refused):
        await doc_send.send(T, "gone", "accounts@atlantis.co.za", confirm=True)
    with pytest.raises(doc_send.Refused, match="isn't on file"):
        await doc_send.send(T, "x1", "accounts@atlantis.co.za", confirm=True)       # another business's
    monkeypatch.setattr("vula.consent.opted_out", lambda tid, phone: True)
    with pytest.raises(doc_send.Refused, match="opted out"):
        await doc_send.send(T, "b74", "0827654321", channel="whatsapp", confirm=True)
    need = await doc_send.send(T, "b74", "Nobody Known", confirm=True)
    assert need["status"] == "need_info" and not db.sent["email"] and not db.sent["wa"]


@pytest.mark.asyncio
async def test_the_assistant_tool_asks_which_letter_then_previews(db, monkeypatch):
    from core.skills import commerce_admin as ca
    monkeypatch.setattr(ca, "_ctx_access", lambda tid, ctx: None)
    skill = ca.CommerceAdminSkill()
    out = await skill._send_document(T, {"document": "our bank letter", "to": "Belekkie Skips"}, {"phone": "27820000000"})
    assert out["status"] == "need_info" and "…6574" in out["message"] and "…7663" in out["message"]
    out = await skill._send_document(T, {"document": "bank letter 7663", "to": "Belekkie Skips"}, {"phone": "27820000000"})
    assert out["preview"] and out["doc_id"] == "b76" and not db.sent["email"]
    assert "send_document" in ca._PREVIEW_FIRST and ca._TOOL_SCOPE["send_document"] == "documents"
