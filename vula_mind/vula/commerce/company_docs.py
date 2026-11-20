"""vula/commerce/company_docs.py — the business's own company and tax documents (20 Nov 2026, Ian).

"A place where the business uploads its company documents, filed properly — SARS documents, company
registration and so on — and the accountant has access." DIGG's were "General Document": two SARS CRA01
address confirmations, a COIDA letter of good standing, a letter saying Aweh Be Lekker is being registered
for VAT. None reached the accountant's folders.

Three categories, recognised in code from the document's own title or summary (never a model's guess alone):
  Company Registration   CIPC: COR14.3 / COR39 / COR15, MOI, share certificates, directors' resolutions,
                         beneficial ownership, annual returns
  Tax / SARS             SARS registration notices (income tax, VAT, PAYE/UIF/SDL), tax compliance / TCS PIN,
                         assessments, VAT201 / EMP201 / EMP501 / IRP5 / ITR14 / IRP6, CRA01, SARS letters
  Compliance Certificate B-BBEE certificate or affidavit, COIDA letter of good standing, UIF, FICA, the
                         bank's confirmation / proof of the business's own bank account

  kind_of(filename, summary)   the category a document's own words name, or None
  better(category, summary, filename, tenant)   at intake: that category over a catch-all or loose guess,
                               for a document that names the business itself (`names_us`)
  proposals(tenant)            documents already filed elsewhere that are one of these — a preview
  refile(tenant, ids, actor)   file them as such (the old category kept), read back, audited

They go in the accountant's folder "00 Company and tax", whatever the year (vula/commerce/finance_folders.py).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

COMPANY = "Company Registration"
TAX = "Tax / SARS"
COMPLIANCE = "Compliance Certificate"
CATEGORIES = (COMPANY, TAX, COMPLIANCE)
GROUP_NAMES = {COMPANY: "Company registration (CIPC)", TAX: "SARS and tax",
               COMPLIANCE: "Compliance and bank letters (B-BBEE, COIDA, UIF, FICA, proof of bank account)"}
# categories a company or tax document may have been given instead (never an invoice, a quote, a contract…)
LOOSE = ("General Document", "Email attachment", "Other file", "Correspondence", "Legal / Property", "Report",
         "Specification", "Business Card", "Brochure / Product Info")

_RULES = [
    # SARS first: an importer/exporter or VAT "registration certificate" issued by SARS is a tax document
    (TAX, re.compile(r"\b(sars|south african revenue|tax clearance|tax compliance|\btcs\b|compliance (status )?pin|"
                     r"vat ?201|vat registration|vat vendor|registered for vat|vat number confirmation|"
                     r"emp ?201|emp ?501|irp ?5|it3\(?a\)?|itr ?14|irp ?6|it ?14|provisional tax|"
                     r"notice of (assessment|registration)|income tax registration|paye registration|"
                     r"(importer|exporter|customs) registration|cra ?01|"
                     r"confirmation of (residential|business) address)\b", re.I)),
    (COMPANY, re.compile(r"\b(cipc|intellectual property commission|cor ?14\.?3|cor ?39|cor ?15\.?\d?|cor ?21|"
                         r"memorandum of incorporation|\bmoi\b|registration certificate|certificate of incorporation|"
                         r"share certificate|securities register|beneficial owner(ship)?|directors'? resolution|"
                         r"written resolution|resolutions? of the directors|annual return)\b", re.I)),
    (COMPLIANCE, re.compile(r"\b(b-?bbee|bbbee|bee certificate|bee affidavit|sworn affidavit.{0,40}b-?bbee|"
                            r"coida|compensation fund|letter of good standing|workmen'?s compensation|"
                            r"\buif\b|unemployment insurance|fica|"
                            r"bank (account )?confirmation( letter)?|account confirmation letter|"
                            r"confirmation of (banking|bank account) details|proof of (bank|banking)( account| details)?|"
                            r"bank letter)\b", re.I)),
]
# words that make it about someone else's business or a sales pitch, not the business's own document
_NOT_OURS = re.compile(r"\b(services? list|we (offer|provide)|provides? (various )?services|pricing|quotation|"
                       r"how to|step-by-step guide|guide on)\b", re.I)


def kind_of(filename: Optional[str], summary: Optional[str]) -> Optional[str]:
    """The company/tax category the document's own title or summary names, or None. A guide about SARS
    or a company offering registrations isn't one."""
    text = f"{filename or ''} {summary or ''}"
    if not text.strip() or _NOT_OURS.search(text):
        return None
    for cat, rx in _RULES:
        if rx.search(text):
            return cat
    return None


def names_us(tenant_id: Optional[str], filename: Optional[str], summary: Optional[str]) -> bool:
    """The document names the business itself (its own names, "X t/a Y" split) — a supplier's B-BBEE
    certificate or bank letter, or a client's resolution, is theirs, not the business's."""
    if not tenant_id:
        return False
    from vula.commerce import bank_rec
    from vula.commerce.books_review import _alike, _words
    try:
        own = set().union(*(_words(n) for n in bank_rec._own_names_now(tenant_id)))
    except Exception:  # noqa: BLE001
        return False
    said = _words(f"{filename or ''} {summary or ''}")
    return any(_alike(a, b) for a in said for b in own)


def better(category: Optional[str], summary: Optional[str], filename: Optional[str] = "",
           tenant_id: Optional[str] = None) -> Optional[str]:
    """At intake: a company/tax category over a catch-all or loose guess — only for a document that names
    the business itself; anything else stands."""
    if category in CATEGORIES or (category and category not in LOOSE):
        return category
    k = kind_of(filename, summary)
    return k if k and names_us(tenant_id, filename, summary) else category


def _client():
    from vula.commerce import service
    return service._client()


def proposals(tenant_id: str) -> List[Dict[str, Any]]:
    """Live documents filed under a loose category whose own words say they're company or tax documents."""
    from vula.commerce.doc_audit import _pages, live
    rows = _pages(lambda: _client().table("vula_filed_documents")
                  .select("id,category,filename,summary,status,fields,created_at,project")
                  .eq("tenant_id", tenant_id).in_("category", list(LOOSE)).order("id"))
    out = []
    for d in rows:
        if not live(d):
            continue
        k = kind_of(d.get("filename"), d.get("summary"))
        if k:
            out.append({"id": d["id"], "filename": d.get("filename"), "category": d.get("category"), "to": k,
                        "ours": names_us(tenant_id, d.get("filename"), d.get("summary")),
                        "to_name": GROUP_NAMES[k], "summary": (d.get("summary") or "")[:240],
                        "date": str((d.get("fields") or {}).get("date") or d.get("created_at") or "")[:10]})
    return sorted(out, key=lambda x: (not x["ours"], x["to"], x["date"]))


def refile(tenant_id: str, ids: Iterable[str], actor: Optional[dict] = None) -> Dict[str, Any]:
    """File the chosen documents under the category their own words name; the old one is kept in
    `fields._recategorised`. Only documents the preview offers are touched. Read back, audited."""
    from vula.commerce import doc_audit
    want = [str(i) for i in dict.fromkeys(ids or []) if i]
    if not want:
        raise doc_audit._refused("Choose at least one document.")
    offered = {p["id"]: p for p in proposals(tenant_id)}
    done, skipped = [], []
    for i in want:
        p = offered.get(i)
        if not p:
            skipped.append(i)
            continue
        doc = doc_audit._doc(tenant_id, i)
        f = dict(doc.get("fields") or {})
        f["_recategorised"] = {"from": doc.get("category"), "to": p["to"], "by": doc_audit._who(actor),
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        back = doc_audit._save_fields(tenant_id, i, f, category=p["to"])
        (done if back.get("category") == p["to"] else skipped).append(i)
    if done:
        doc_audit._audit(tenant_id, actor, "documents_refiled_company", ids=done)
    return {"ok": not skipped, "refiled": done, "skipped": skipped}
