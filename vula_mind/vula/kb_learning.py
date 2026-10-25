"""Knowledge questions: ask instead of guessing, and keep the data to learn from (2026-10-24).

Ian: "I just want it to be accurate, and if it can't, ask the right question to get to the right
answer ... so over time it gets better, but to learn we need proper data."

The retrieval diagnostic (evals/retrieval_diag.py) found that many misses were questions with no
single right document: "What is the total amount I need to pay for this receipt?" fits every
receipt on file, and search picks one. Guessing between look-alike money documents is how a
wrong figure gets stated with confidence. So for a staff member's question:

  * ambiguous() — the question names a kind of money document (invoice, receipt, statement…)
    without saying which, and the search found two or more such documents that nothing in the
    question tells apart: Vula asks which one, listing them by supplier, amount and date, rather
    than answering from whichever ranked first.
  * take_pick() — the reply "2" (or "the second", or the supplier's name) answers the original
    question from that document only (answer_pinned).
  * note_feedback() — "no, that's wrong" straight after an answer marks that answer wrong.

Every staff knowledge question is recorded in vula_kb_turns (migration 222): the documents found,
whether Vula answered, asked or found nothing, the pick, and the feedback. A pick is a confirmed
question -> document pair; those become test cases and retrieval hints over time.

Customers are never asked to choose between the business's documents and are never recorded
here. The asker is stored as a hash; document text is never stored. Everything fails open: a
lookup or write error means Vula answers exactly as it did before this existed.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

INSIDER_ROLES = {"owner", "manager", "admin", "staff", "sales_rep"}
CLARIFY_WINDOW = timedelta(minutes=30)
FEEDBACK_WINDOW = timedelta(minutes=15)
MAX_OPTIONS = 3

# A question about "the invoice" / "this receipt" — a kind of money document, not a named one.
_DOC_KIND_RE = re.compile(
    r"\b(invoices?|receipts?|statements?|quot(?:e|es|ation|ations)|purchase orders?|"
    r"(?:deposit|payment|fuel|bank) slips?|delivery notes?|bills?|credit notes?|remittances?|proof of payment|pop)\b",
    re.I)
# Questions over several documents at once, or the newest one, are not a choice between them.
_NOT_A_CHOICE_RE = re.compile(
    r"\b(all|every|each|list|how many|both|compare|latest|last|most recent|newest|"
    r"this (?:month|week|year)|per month)\b", re.I)
# Words that say what is wanted from the document, not which document it is.
_GENERIC_WORDS = {
    "invoice", "invoices", "receipt", "receipts", "statement", "statements", "quote", "quotes",
    "quotation", "order", "orders", "slip", "slips", "bill", "bills", "credit", "note", "notes",
    "remittance", "proof", "payment", "payments", "pop", "total", "amount", "due", "date", "paid",
    "pay", "owe", "owed", "number", "account", "much", "what", "when", "which", "where", "who",
    "the", "this", "that", "for", "and", "with", "from", "our", "your", "need", "does", "did",
    "was", "are", "is", "how", "price", "cost", "vat", "incl", "excl", "include", "includes",
    "reference", "ref", "bank", "details", "supplier", "customer", "products", "supplied",
    "charged", "charge", "rate", "there", "have", "has", "tell", "please", "can", "you",
}


def is_insider(role: Optional[str]) -> bool:
    return (role or "").strip().lower() in INSIDER_ROLES


def asker_key(tenant_id: str, who: str) -> str:
    """A stable, non-reversible id for the person asking (a phone number is never stored)."""
    digits = "".join(ch for ch in (who or "") if ch.isdigit()) or (who or "")
    return hashlib.sha256(f"{tenant_id}:{digits}".encode()).hexdigest()[:32]


def _client():
    from vula.commerce import service
    return service._client()


# ── Labels: which document is which, in words a person recognises ──────────────────────────

def _money_kind(text: str) -> Optional[str]:
    m = _DOC_KIND_RE.search(text or "")
    return m.group(1).lower().split()[-1].rstrip("s") if m else None


def _date_of(fields: Dict[str, Any], created_at: Optional[str]) -> str:
    for k in ("date", "invoice_date", "quote_date", "document_date", "doc_date", "payment_date",
              "statement_date", "delivery_date"):
        v = fields.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v)[:10]).strftime("%-d %b %Y")
            except ValueError:
                return str(v)[:20]
    if created_at:
        try:
            return "filed " + datetime.fromisoformat(str(created_at)[:10]).strftime("%-d %b %Y")
        except ValueError:
            pass
    return ""


def describe_docs(tenant_id: str, doc_ids: Iterable) -> Dict[str, Dict[str, Any]]:
    """{doc_id: {label, kind, party}} for documents that were filed with extracted fields.
    Fail-open ({})."""
    ids = sorted({str(d) for d in doc_ids if d})
    if not ids:
        return {}
    try:
        rows = (_client().table("vula_filed_documents")
                .select("doc_id,filename,category,fields,created_at")
                .eq("tenant_id", tenant_id).in_("doc_id", ids).execute().data or [])
    except Exception as exc:
        logger.debug("describe_docs skipped: %s", exc)
        return {}
    from vula.commerce.service import _document_amount
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        f = r.get("fields") or {}
        party = next((str(f[k]) for k in ("supplier", "supplier_name", "vendor", "vendor_name",
                                           "payee_name", "payee", "customer", "client")
                      if isinstance(f.get(k), str) and f.get(k).strip()), "")[:60]
        kind = _money_kind(r.get("category") or "") or _money_kind(r.get("filename") or "")
        bits = [b for b in (party, _rand(_document_amount(f)), _date_of(f, r.get("created_at"))) if b]
        head = (r.get("category") or "Document").strip().capitalize()
        out[str(r["doc_id"])] = {
            "label": f"{head} — {', '.join(bits)}" if bits else (r.get("filename") or head),
            "kind": kind, "party": party,
        }
    return out


def _rand(amount: Any) -> str:
    try:
        return f"R{float(amount):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return ""


# ── Is the question a choice between documents? ─────────────────────────────────────────────

def _question_terms(question: str) -> List[str]:
    """The words that could say WHICH document: names, numbers, codes."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]*[A-Za-z0-9]|[A-Za-z0-9]", question or "")
    out: List[str] = []
    for w in words:
        lw = w.lower()
        if lw in _GENERIC_WORDS or len(lw) < 3 or lw in out:
            continue
        out.append(lw)
    return out


def ambiguous(tenant_id: str, question: str, chunks: List[Dict[str, Any]],
              info: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, str]]:
    """The documents to ask between, or [] when the question can be answered as it stands.

    Ask only when ALL hold: the question names a kind of money document without saying which
    one; it isn't about several at once or the latest; the search returned two or more such
    documents; and no name, number or code in the question matches just one of them (a term
    matching some of them narrows the choice to those)."""
    if not question or not _DOC_KIND_RE.search(question) or _NOT_A_CHOICE_RE.search(question):
        return []
    docs: Dict[str, Dict[str, Any]] = {}
    for c in chunks or []:
        did = str(c.get("doc_id") or "")
        if not did:
            continue
        d = docs.setdefault(did, {"doc_id": did, "filename": c.get("filename") or "", "text": ""})
        d["text"] += " " + (c.get("text") or "")
    if len(docs) < 2:
        return []
    info = describe_docs(tenant_id, docs) if info is None else info
    cands = []
    for did, d in docs.items():
        meta = info.get(did) or {}
        kind = meta.get("kind") or _money_kind(d["filename"]) or _money_kind(d["text"][:400])
        if not kind:
            continue
        cands.append({**d, "label": meta.get("label") or d["filename"] or "a document",
                      "party": meta.get("party") or ""})
    if len(cands) < 2:
        return []
    for term in _question_terms(question):
        hit = [c for c in cands if term in (c["text"] + " " + c["label"] + " " + c["filename"]).lower()]
        if len(hit) == 1:
            return []
        if 1 < len(hit) < len(cands):
            cands = hit
    if len(cands) < 2 or len({c["label"] for c in cands}) < 2:
        return []
    return [{"doc_id": c["doc_id"], "label": c["label"]} for c in cands[:MAX_OPTIONS]]


def clarify_message(options: List[Dict[str, str]]) -> str:
    lines = [f"{i}. {o['label']}" for i, o in enumerate(options, 1)]
    return ("I found more than one document that could be the one you mean, so I don't want to "
            "guess. Which one?\n" + "\n".join(lines) + "\n\nReply with the number.")


_ORDINALS = {"first": 0, "1st": 0, "eerste": 0, "second": 1, "2nd": 1, "tweede": 1,
             "third": 2, "3rd": 2, "derde": 2}


def resolve_pick(text: str, options: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Which option a short reply chose: "2", "option 2", "the second one", or a word that names
    only one of them ("Atlantis"). None for anything else — that is a new message, not a pick."""
    t = (text or "").strip().lower()
    if not t or len(t) > 60 or not options:
        return None
    m = re.fullmatch(r"(?:option|number|no\.?|nr\.?|#)?\s*([1-9])\s*[.)]?\s*(?:please|pls|thanks)?[.!]*", t)
    if m:
        i = int(m.group(1)) - 1
        return options[i] if i < len(options) else None
    for word, i in _ORDINALS.items():
        if re.search(rf"\b{word}\b", t) and len(t.split()) <= 5:
            return options[i] if i < len(options) else None
    if len(t.split()) <= 6:
        named = {o["doc_id"]: o for w in re.findall(r"[a-z0-9][a-z0-9\-/.]{3,}", t)
                 for o in options if w in o["label"].lower() and w not in _GENERIC_WORDS}
        if len(named) == 1:   # words naming exactly one option, and no other
            return next(iter(named.values()))
    return None


# ── Feedback on the last answer ──────────────────────────────────────────────────────────────

_WRONG_RE = re.compile(
    r"^\s*(?:no+\b[,.!:;—–-]*|nope\b|wrong\b|that'?s (?:wrong|not (?:it|right|correct|the one))|"
    r"incorrect\b|not (?:that|the right) one|nee\b|verkeerd\b)", re.I)
_RIGHT_RE = re.compile(
    r"^\s*(?:thanks|thank you|thx|perfect|great|correct|spot on|that'?s (?:it|right|correct)|"
    r"dankie|baie dankie)\b[\s!.👍🙏]*$", re.I)


def detect_feedback(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t or len(t) > 200:
        return None
    if _WRONG_RE.match(t):
        return "corrected"
    if _RIGHT_RE.match(t):
        return "confirmed"
    return None


# ── Storage ──────────────────────────────────────────────────────────────────────────────────

def record_turn(tenant_id: str, who: str, *, role: Optional[str], question: str, outcome: str,
                candidates: Optional[List[Dict[str, str]]] = None, answer_docs: Iterable = (),
                skill: Optional[str] = None, channel: str = "whatsapp") -> Optional[str]:
    """One staff knowledge question. Returns the row id, or None (not an insider, or the write
    failed — never raises)."""
    if not (tenant_id and who and question and is_insider(role)):
        return None
    try:
        row = {"tenant_id": tenant_id, "asker": asker_key(tenant_id, who),
               "role": (role or "").lower(), "channel": channel, "question": question[:1000],
               "outcome": outcome, "skill": skill,
               "candidates": [{"doc_id": c["doc_id"], "label": c.get("label", "")[:200]}
                              for c in (candidates or [])][:8],
               "answer_docs": sorted({str(d) for d in answer_docs if d})[:8]}
        res = _client().table("vula_kb_turns").insert(row).execute().data or []
        return res[0].get("id") if res else None
    except Exception as exc:
        logger.debug("kb turn not recorded: %s", exc)
        return None


def _latest_turn(tenant_id: str, who: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_kb_turns")
            .select("id,question,candidates,outcome,picked_doc,feedback,created_at")
            .eq("tenant_id", tenant_id).eq("asker", asker_key(tenant_id, who))
            .order("created_at", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None


def _age(row: Dict[str, Any]) -> timedelta:
    try:
        at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - at
    except Exception:
        return timedelta.max


def take_pick(tenant_id: str, who: str, text: str) -> Optional[Dict[str, str]]:
    """If this person's last knowledge question got a "which one?" in the last 30 minutes and
    this message picks an option: record the pick and return {question, doc_id, label}."""
    if not (tenant_id and who and text) or len(text.strip()) > 60 or len(text.split()) > 6:
        return None
    try:
        last = _latest_turn(tenant_id, who)
        if (not last or last.get("outcome") != "clarify" or last.get("picked_doc")
                or _age(last) > CLARIFY_WINDOW):
            return None
        pick = resolve_pick(text, last.get("candidates") or [])
        if not pick:
            return None
        _client().table("vula_kb_turns").update({
            "picked_doc": pick["doc_id"], "picked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", last["id"]).execute()
        return {"question": last["question"], "doc_id": pick["doc_id"], "label": pick["label"],
                "turn_id": last["id"]}
    except Exception as exc:
        logger.debug("clarification pick skipped: %s", exc)
        return None


def note_feedback(tenant_id: str, who: str, text: str) -> Optional[str]:
    """Mark this person's last answer (within 15 minutes) right or wrong when the message says so.
    Never consumes the message: "no, the June one" is still answered as a new question."""
    kind = detect_feedback(text)
    if not (kind and tenant_id and who):
        return None
    try:
        last = _latest_turn(tenant_id, who)
        if (not last or last.get("outcome") != "answered" or last.get("feedback")
                or _age(last) > FEEDBACK_WINDOW):
            return None
        _client().table("vula_kb_turns").update({
            "feedback": kind, "feedback_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", last["id"]).execute()
        return kind
    except Exception as exc:
        logger.debug("kb feedback not recorded: %s", exc)
        return None


async def answer_pinned(tenant_id: str, pick: Dict[str, str], metadata: Dict[str, Any],
                        conversation_history: str = ""):
    """Answer the original question from the picked document only (the reasoning skill, with the
    search restricted to that document). Returns the SkillOutput."""
    from core.skills.base import SkillInput
    from core.skills.loader import get_skill
    meta = {**(metadata or {}), "kb_doc_id": pick["doc_id"], "kb_doc_label": pick.get("label", "")}
    return await get_skill("reasoning")(SkillInput(
        question=pick["question"], tenant_id=tenant_id,
        conversation_history=conversation_history, metadata=meta))
