"""WhatsApp review, 6 Oct 05:18–05:45 (digg-demo, owner 27645755210), from the turn record.

1. "What is the current total invoices count at jack hammer" → web search ("current"), which
   answered that the web had nothing.
2. "Can you please put all jack hammer invoice/expense and Matirial bought lit in a sharable PDF"
   → file_parse ("pdf"), which typed a list, cut off mid-item, and sent no PDF.
3. "Proceed" → the general reasoning skill, which re-typed the list and cut it off again.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from core.hrm.orchestrator import HRMOrchestrator
from core.skills.base import looks_like_supplier_history_question
from vula.api import whatsapp as wa
from vula.commerce import service

TID, PHONE = "digg-demo", "27645755210"
COUNT_Q = "What is the current total invoices count at jack hammer"
PDF_Q = "Can you please put all jack hammer invoice/expense and Matirial bought lit in a sharable PDF"


@pytest.mark.parametrize("q", [COUNT_Q, PDF_Q])
def test_the_business_own_invoices_go_to_the_document_search(q):
    assert looks_like_supplier_history_question(q)
    assert HRMOrchestrator()._route_with_reason(q, TID) == ("email_admin", "supplier_history")


@pytest.mark.parametrize("q,skill", [("search online for the latest tile prices", "web_search"),
                                     ("summarise this pdf", "file_parse")])
def test_real_web_and_file_requests_still_go_there(q, skill):
    assert HRMOrchestrator()._route_with_reason(q, TID)[0] == skill


def test_proceed_carries_on_with_the_last_request():
    wa._LAST_ASK.clear()
    assert wa._resolve_go_ahead(TID, PHONE, PDF_Q) == PDF_Q
    assert wa._resolve_go_ahead(TID, PHONE, "Proceed") == PDF_Q
    assert wa._resolve_go_ahead(TID, PHONE, "go ahead please") == PDF_Q
    assert wa._resolve_go_ahead(TID, PHONE, "yes") == "yes"            # may be confirming a preview
    wa._LAST_ASK[(TID, PHONE)] = (PDF_Q, time.time() - 3600)          # an hour ago: too old
    assert wa._resolve_go_ahead(TID, PHONE, "Proceed") == "Proceed"


RESULT = {"status": "found", "total_amount_cents": 1000000, "resolved_supplier": "GARDENS HANDIMAN CENTRE",
          "_export_rows": [
              {"date": "2026-10-05", "ref": "23-247517", "total_cents": 87000, "is_refund": False},
              {"date": "2026-10-01", "ref": "21-367569", "total_cents": 165600, "is_refund": True},
              {"date": "2026-10-01", "ref": "23-247000", "total_cents": 165600, "is_refund": False}],
          "_materials_all": [{"description": "CEMENT 50KG PPC 42.5N", "quantity": 18, "spend_cents": 286200}]}


def test_the_pdf_has_every_invoice_the_refund_negative_and_the_materials():
    import fitz
    from vula.commerce.xlsx import render_supplier_history_pdf
    data = render_supplier_history_pdf(RESULT, "GARDENS HANDIMAN CENTRE", "DIGG")
    text = "\n".join(p.get_text() for p in fitz.open(stream=data, filetype="pdf"))
    assert "23-247517" in text and "21-367569" in text and "CEMENT 50KG" in text
    assert "R870.00" in text and "R1,656.00" in text
    assert "total R870.00" in text                                     # 870 + 1656 − 1656


@pytest.mark.asyncio
async def test_a_pdf_request_sends_a_pdf_not_a_spreadsheet(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "DIGG")
    with patch("vula.api.whatsapp._send_invoice_document", AsyncMock(return_value=True)) as send:
        assert await service.send_supplier_history_xlsx(TID, PHONE, PDF_Q, RESULT, "GARDENS HANDIMAN CENTRE")
    args, kwargs = send.await_args
    assert args[2].endswith(".pdf") and kwargs["content_type"] == "application/pdf"
    assert service.export_label(PDF_Q) == "a PDF"
    assert service.export_label("in excel please") == "an Excel file"


def _history(n_inv=45, n_mat=30):
    rows = [{"date": f"2026-09-{(i % 28) + 1:02d}", "ref": f"23-24{i:04d}", "total_cents": 10000 + i,
             "vat_cents": 1304, "is_refund": False, "party": "GARDENS HANDIMAN CENTRE"} for i in range(n_inv)]
    rows.append({"date": "2026-10-01", "ref": "21-367569", "total_cents": -165600, "vat_cents": -21600,
                 "is_refund": True, "party": "GARDENS HANDIMAN CENTRE"})
    mats = [{"description": f"ITEM {i}", "quantity": i + 1, "spend_cents": 5000 * (n_mat - i),
             "spend": f"R{50 * (n_mat - i):,.2f}"} for i in range(n_mat)]
    priced = sum(r["total_cents"] for r in rows)
    matches = [{"filename": r["ref"], "filed_at": r["date"], "amount": r["total_cents"] / 100,
                "is_refund": r["is_refund"], "party": r["party"]} for r in rows[:30]]
    return {"status": "found", "total_amount_cents": priced, "total_amount": f"R{priced / 100:,.2f}",
            "total_matches": len(rows), "matches_with_amount": len(rows), "matches": matches,
            "resolved_supplier": "GARDENS HANDIMAN CENTRE", "_export_rows": rows,
            "materials": mats[:12], "materials_distinct": n_mat, "_materials_all": mats}


def test_a_full_breakdown_lists_every_invoice_and_material_with_a_written_summary():
    res = _history()
    text = service.format_supplier_history_reply(
        res, question="Please give me a full break down of all jack hammer invoices", xlsx_sent=False)
    assert text.count("\n• ") == 46 + 30                          # every invoice + every material
    assert "…and" not in text and "more item" not in text
    assert "*Summary*" in text and "Period: 2026-09-01 to 2026-10-01" in text
    assert "45 invoices, 1 refund" in text and "Refunds: −R1,656.00" in text
    total = sum(r["total_cents"] for r in res["_export_rows"])
    assert f"Total R{total / 100:,.2f}" in text
    assert "By month: 2026-09" in text


def test_a_simple_question_keeps_the_short_answer():
    text = service.format_supplier_history_reply(_history(), question="what did we spend at jack hammer")
    assert "*Summary*" not in text and "…and 16 more" in text


def test_the_full_text_goes_out_whole_in_several_messages():
    text = service.format_supplier_history_reply(_history(120, 80), question="full breakdown")
    parts = wa._split_for_whatsapp(text)
    assert len(parts) > 1 and all(len(p) <= 3900 for p in parts)
    assert "\n".join(parts).count("• ") == text.count("• ")
