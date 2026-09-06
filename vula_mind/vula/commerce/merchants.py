"""vula/commerce/merchants.py — work out WHO a bank transaction was paid to, once.

2026-09-06, Ian: "when scanning in the bank statements, why can Vula research crazy store and be
able to allocate transaction based on good bought?"

Measured against production, the gap was worse than missing knowledge — accounting.categorize_batch
filed the SAME merchant differently on different lines, because it decides per transaction in
batches of 40 (each a separate model call, so temperature=0 buys no consistency across them) and
its entire input per line is `[out] R99.90 — The Crazy Store Tablevi Cape Town (Card 572)`. It is
never told what that shop sells. off-the-hook's real books:

    Crazy Store      x8  ->  4 cost_of_sales, 4 owner_drawings
    Dis-Chem         x8  ->  7 owner_drawings, 1 other_expense
    Checkers Sixty60 x7  ->  4 cost_of_sales, 2 owner_drawings, 1 other_expense
    Pick n Pay       x6  ->  5 owner_drawings, 1 bank_charges
    Table Bay        x9  ->  6 cost_of_sales, 1 sales   (a payment OUT filed as income)

So the unit of decision moves from the transaction to the MERCHANT: identify it, research it once,
cache the verdict, apply it to every transaction that shares the key. 322 money-out rows for
off-the-hook are only 120 distinct merchants; digg-demo's 62 are 26.

Research tells us what a business SELLS. It cannot tell us whether a given purchase was stock or
the owner's own groceries — Dis-Chem is legitimately owner_drawings for Staci, and Pick n Pay
could be either. Those come back 'ambiguous' and the owner is asked once, ever.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


# Card/branch/channel noise a South African statement staples onto the merchant name. Stripped
# BEFORE tokenising so "(Card 572)" and a branch suffix don't become part of the identity.
_NOISE_RE = re.compile(
    r"\(card\s*\d+\)|card\s*\d{3,}|\bpos\b|\bref\b|"
    r"immediate\s+external\s+payment|external\s+payment|internal\s+payment|"
    r"payment\s+received|app\s+payment|app\s+transfer|debit\s+order|"
    r"\b\d{6,}\b",                       # long account/reference numbers
    re.I,
)

# The BANK's own channel wording, not a counterparty. Dropped wherever it appears, because a
# statement puts it before the real payee: "FNB App Rtc Pmt To Digg Bricks-Boards" is a payment
# to Digg Bricks, and "Payshap Account Off-Us Skipp Rubble Removal" is one to Skipp Rubble.
# Verified necessary against digg-demo's real statement, where treating the channel as the
# merchant produced a single meaningless "fnb app" covering 26 unrelated payments — and split
# the same channel two ways ("FNB App Transfer To Pay" -> 'fnb pay').
_CHANNEL = {
    "fnb", "absa", "nedbank", "standardbank", "payshap", "rtc", "pmt", "eft", "app",
    "immediate", "external", "internal", "transfer", "transfers", "send", "sent", "primed",
    "instant", "online", "banking", "mobile",
}

# Connector/filler words that are never part of a counterparty's name.
_NON_IDENTITY = {
    "off", "out", "via", "from", "and", "the", "new", "old", "account",
    "uncategorised", "uncategorized", "proof", "whatsapp", "misc", "other", "general",
}

# Words that are meaningless as a WHOLE identity but are legitimate PARTS of one. "pay" cannot
# be stripped outright — "Pick n Pay Table View" would become 'pick table' and each branch would
# fragment into its own merchant. So it is only rejected when it is the entire key, which is
# what "FNB App Transfer To Pay" reduces to.
_JUNK_ALONE = {"pay", "payment", "account", "fees", "service", "cash", "money"}


def merchant_key(description: Optional[str]) -> str:
    """A stable identity for the party a transaction was paid to, or "" when the statement
    line names no counterparty at all.

    The join key for merchant profiles, learned rules and the bulk-review grouping — one
    definition used everywhere, so a rule learned from one spelling fires for the others.
    "The Crazy Store Tablevi Cape Town (Card 572)" and "CRAZY STORE TABLE VIEW" both give
    'crazy store'.

    Returning "" matters as much as returning a key: "FNB App Transfer To Pay" identifies
    nobody, and inventing an identity for it would group unrelated payments under one account.
    Those fall through to the existing per-transaction path instead.
    """
    from vula.commerce.accounting import _STOP
    text = _NOISE_RE.sub(" ", (description or "").lower())
    toks = [t for t in re.findall(r"[a-z][a-z0-9]{2,}", text)
            if t not in _STOP and t not in _CHANNEL]
    toks = [t for t in toks if t not in _NON_IDENTITY]
    key = " ".join(toks[:2])
    return "" if key in _JUNK_ALONE else key


# Retail categories where knowing the trade does NOT settle the account: the same shop is stock
# for one owner and personal spend for another, or both for the same owner in the same month.
_AMBIGUOUS_TRADES = (
    "supermarket", "grocer", "pharmacy", "chemist", "convenience", "general retail",
    "variety", "department store", "hardware", "fuel", "petrol", "filling station",
    "restaurant", "takeaway", "cafe", "liquor", "clothing", "online retail", "marketplace",
)


def get_profile(tenant_id: str, key: str) -> Optional[dict]:
    if not key:
        return None
    try:
        rows = (_client().table("commerce_merchant_profiles").select("*")
                .eq("tenant_id", tenant_id).eq("merchant_key", key).limit(1).execute().data or [])
    except Exception as exc:
        log.debug("merchant profile read skipped (run migration 154?): %s", exc)
        return None
    return rows[0] if rows else None


def save_profile(tenant_id: str, key: str, **fields: Any) -> None:
    row = {"tenant_id": tenant_id, "merchant_key": key, **fields}
    try:
        _client().table("commerce_merchant_profiles").upsert(
            row, on_conflict="tenant_id,merchant_key").execute()
    except Exception as exc:
        log.debug("merchant profile write skipped (run migration 154?): %s", exc)


async def _search_pages(name: str, limit: int = 3) -> str:
    """Candidate pages about this merchant, fetched SAFELY.

    Uses reference_url.safe_fetch_html, NOT web_search._fetch_text: the latter accepts any URL,
    follows redirects and has no private-IP/metadata-endpoint guard, which is tolerable for a
    search a person explicitly asked for and not for a path that fetches search-result URLs
    automatically on every statement import. Same choice shared_link.py made.
    """
    from core.skills.web_search import _ddg_search
    from vula.commerce.reference_url import safe_fetch_html

    try:
        hits = [h for h in (await _ddg_search(f"{name} South Africa business", limit=limit) or [])
                if h.get("url")][:limit]
    except Exception as exc:
        log.debug("merchant search failed for %r: %s", name, exc)
        return ""

    parts: List[str] = []
    for h in hits:
        try:
            html = await safe_fetch_html(h["url"])
        except Exception as exc:                      # UnsafeUrlError or any httpx failure
            log.debug("skipped %s: %s", h.get("url"), exc)
            continue
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            parts.append(f"{h.get('title', '')}: {text[:900]}")
    return "\n\n".join(parts)


async def research_merchant(tenant_id: str, key: str, sample_description: str,
                            accounts: Optional[List[dict]] = None) -> Optional[dict]:
    """Identify a merchant and propose an account. Caches the result — INCLUDING a miss, so an
    unidentifiable merchant is never researched twice (the commerce_geo_cache rule). Returns the
    saved profile, or None when the lookup failed transiently and should be retried later."""
    from vula.commerce.accounting import ensure_chart

    if not key:
        return None
    accounts = accounts if accounts is not None else ensure_chart(tenant_id)
    expense = [a for a in accounts if a.get("type") in ("expense", "equity")]
    listing = "\n".join(f"- {a['code']}: {a['name']}" for a in expense)

    pages = await _search_pages(key)
    verdict = await _classify(key, sample_description, pages, listing)
    if verdict is None:
        return None                                   # transient — don't poison the cache

    code = (verdict.get("suggested_account_code") or "").strip().lower()
    if code and not any(a["code"] == code for a in expense):
        code = ""
    trade = (verdict.get("what_they_sell") or "").strip()
    confidence = (verdict.get("confidence") or "").strip().lower()
    if confidence not in ("confident", "ambiguous"):
        confidence = "ambiguous"
    # Structural override: whatever the model claims, a supermarket or pharmacy cannot be
    # settled from the trade alone. Same deterministic-backstop rule the rest of this codebase
    # uses where a prompt instruction has already proved insufficient.
    if any(w in trade.lower() for w in _AMBIGUOUS_TRADES):
        confidence = "ambiguous"
    if not code:
        confidence = "ambiguous"

    save_profile(
        tenant_id, key,
        display_name=(verdict.get("display_name") or key)[:120],
        what_they_sell=trade[:300] or None,
        account_code=code or None,
        confidence=confidence,
        decided_by="research",
        researched_at=_now(),
    )
    return get_profile(tenant_id, key)


async def _classify(key: str, sample_description: str, pages: str,
                    account_listing: str) -> Optional[dict]:
    """Ask the model who this is. Returns None on failure (retryable), a dict otherwise."""
    import litellm
    from core.llm_router import resolve_generation_route
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE, fence

    prompt = (
        "You identify merchants on South African bank statements for a small business's books.\n"
        + UNTRUSTED_CONTENT_RULE
        + "\nGiven the merchant name and any web pages about it, say what the business SELLS and "
          "which account its purchases usually belong to.\n\n"
          "Expense accounts available:\n" + account_listing + "\n\n"
          'Reply with ONLY JSON: {"display_name": str, "what_they_sell": str, '
          '"suggested_account_code": str, "confidence": "confident"|"ambiguous"}\n'
          'Use "ambiguous" whenever purchases there could reasonably be either business stock OR '
          "the owner's personal spending — supermarkets, pharmacies, fuel, general retail. Only "
          'use "confident" for a trade that is unmistakably one or the other (a seafood '
          "wholesaler, a packaging supplier, an accountant).\n"
        + fence("MERCHANT NAME", f"{key} (as it appears: {sample_description[:160]})")
        + fence("WEB PAGES", pages[:4000])
    )
    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="txn_categorize")
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=400, api_key=api_key, api_base=api_base)
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("merchant classification failed for %r: %s", key, exc)
        return None
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        out = json.loads(raw[i:j + 1])
    except Exception:
        try:
            import json_repair
            out = json_repair.loads(raw[i:j + 1])
        except Exception:
            return None
    return out if isinstance(out, dict) else None


def apply_profiles(tenant_id: str, txns: List[dict]) -> Dict[int, Dict[str, Any]]:
    """Allocations for whichever transactions have a decided merchant profile.

    Returns {index: {"account_code", "source"}}. Deciding per merchant rather than per line is
    what removes the Crazy Store / Dis-Chem scatter: one verdict, applied to the whole group.
    Money-IN is left alone — a credit is a customer paying, and merchant identity says nothing
    useful about which income account that is.
    """
    keys = {}
    for i, t in enumerate(txns):
        if (t.get("direction") or "") != "out":
            continue
        k = merchant_key(t.get("description"))
        if k:
            keys.setdefault(k, []).append(i)
    if not keys:
        return {}
    try:
        rows = (_client().table("commerce_merchant_profiles").select("*")
                .eq("tenant_id", tenant_id).in_("merchant_key", list(keys)).execute().data or [])
    except Exception as exc:
        log.debug("merchant profile bulk read skipped (run migration 154?): %s", exc)
        return {}

    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        code = r.get("account_code")
        # 'ambiguous' profiles deliberately allocate nothing until the owner has answered —
        # that is the whole point of asking rather than guessing.
        if not code or r.get("confidence") == "ambiguous" and r.get("decided_by") != "owner":
            continue
        for i in keys.get(r.get("merchant_key") or "", []):
            out[i] = {"account_code": code, "source": "merchant"}
    return out


# An allocation nobody should overwrite: the owner set it by hand, or it came from a
# deterministic match (a receipt, a worker, an invoice) that is stronger evidence than any
# merchant-level guess.
AUTHORITATIVE = ("owner", "receipt", "labour", "matched", "skipped")


def reallocatable(tenant_id: str, limit: int = 5000) -> List[dict]:
    """Money-out transactions a merchant verdict is allowed to (re)file.

    NOT bank_review.pending_txns, which returns only categorized_by in ('default','asked') —
    the rows where Vula gave up. The transactions this feature exists for are the opposite:
    already allocated by the model, just allocated INCONSISTENTLY. off-the-hook's 176 scattered
    rows are all categorized_by='ai', so a pending-only scope would never have reached a single
    one of them.
    """
    try:
        rows = (_client().table("commerce_bank_transactions").select("*")
                .eq("tenant_id", tenant_id).eq("direction", "out")
                .limit(limit).execute().data or [])
    except Exception as exc:
        log.debug("reallocatable read skipped: %s", exc)
        return []
    return [r for r in rows if (r.get("categorized_by") or "") not in AUTHORITATIVE]


def unknown_merchants(tenant_id: str, txns: List[dict]) -> List[tuple]:
    """(merchant_key, sample_description, count) for money-out merchants with no profile yet —
    the work list for the background research pass."""
    counts: Dict[str, List[Any]] = {}
    for t in txns:
        if (t.get("direction") or "") != "out":
            continue
        k = merchant_key(t.get("description"))
        if not k:
            continue
        entry = counts.setdefault(k, [t.get("description") or "", 0])
        entry[1] += 1
    if not counts:
        return []
    try:
        known = {r["merchant_key"] for r in
                 (_client().table("commerce_merchant_profiles").select("merchant_key")
                  .eq("tenant_id", tenant_id).in_("merchant_key", list(counts))
                  .execute().data or [])}
    except Exception as exc:
        log.debug("merchant profile existence check skipped: %s", exc)
        known = set()
    return sorted(((k, v[0], v[1]) for k, v in counts.items() if k not in known),
                  key=lambda x: x[2], reverse=True)
