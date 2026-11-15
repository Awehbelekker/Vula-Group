"""
vula/commerce/profile_from_site.py — draft the business-profile answers from the business's own
website (5 Nov 2026, Ian: "get as much details [from their website], but when the client uses
their number it should ask the questions").

"Import from your website" (site_import.py) already reads the site's pages into the knowledge
base. This takes the pages most likely to hold profile facts (about, contact, delivery,
shipping, returns, FAQ, terms, the home page) and asks the cheap model, once, to answer the
profile questions (business_profile.questions) ONLY from that text — each answer with the exact
sentence it came from. An answer is kept only when that sentence is really on the page
(whitespace and case aside): a figure or policy the model made up has no sentence to point at.

The kept answers are drafts (business_profile.save_drafts), never answers: on WhatsApp the
owner is asked "Your website says … — reply yes to keep it, or send the right answer", and only
then does a customer hear it. Nothing is sent anywhere; the pages are the tenant's own public site.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Pages read first (path or title), then the rest in the order the import found them.
_USEFUL = re.compile(r"about|contact|deliver|shipping|returns?|refund|faq|question|terms|policy|"
                     r"hours|find[-_ ]us|location|service|pricing|how[-_ ]it[-_ ]works", re.I)
_MAX_CHARS = 14000          # what goes to the model, in all
_PER_PAGE = 3500

_PROMPT = """You read a South African business's own {what} and answer questions about the business.
Use ONLY the {text} given. If the text does not clearly answer a question, leave it out —
never guess, never use general knowledge, never invent prices, hours or policies.

For each question you can answer, give:
- "answer": a short answer in the business's own words (one or two sentences),
- "quote": the exact sentence from the {text} that says it, copied character for character,
- "url": where it is (the "Page:" line above that text, copied exactly).

Questions (key: question):
{questions}

Reply with JSON only: {{"answers": {{"<key>": {{"answer": "...", "quote": "...", "url": "..."}}}}}}"""


def pick_pages(pages: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """(url, title, text) pages, the useful ones first, trimmed to what the model reads."""
    useful = [p for p in pages if _USEFUL.search(p[0]) or _USEFUL.search(p[1] or "")]
    home = [p for p in pages if re.match(r"^https?://[^/]+/?$", p[0])]
    rest = [p for p in pages if p not in useful and p not in home]
    out, used = [], 0
    for url, title, text in home + useful + rest:
        if used >= _MAX_CHARS:
            break
        chunk = text[:min(_PER_PAGE, _MAX_CHARS - used)]
        out.append((url, title, chunk))
        used += len(chunk)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def grounded(drafts: Dict[str, Any], pages: List[Tuple[str, str, str]], valid: set) -> Dict[str, Dict[str, str]]:
    """Only the answers whose quote is really on one of the pages (and long enough to mean
    something), with the url of the page it was found on."""
    texts = [(url, _norm(text)) for url, _, text in pages]
    out: Dict[str, Dict[str, str]] = {}
    for key, d in (drafts or {}).items():
        if key not in valid or not isinstance(d, dict):
            continue
        answer, quote = str(d.get("answer") or "").strip(), _norm(str(d.get("quote") or ""))
        if not answer or len(quote) < 12:
            continue
        hit = next((url for url, t in texts if quote in t), None)
        if hit:
            out[key] = {"answer": answer, "quote": str(d.get("quote")).strip(), "url": hit}
    return out


# What is being read: the wording for the model, and the label the text is fenced under.
_SOURCES = {"website": ("website", "website text", "WEBSITE TEXT"),
            "documents": ("company documents", "document text", "DOCUMENT TEXT")}


async def draft_from_pages(tenant_id: str, pages: List[Tuple[str, str, str]],
                           source: str = "website", only_keys: Optional[set] = None) -> Dict[str, Dict[str, str]]:
    """Ask the model once; return the grounded drafts for the questions still unanswered.
    `source`: "website" (pages of the business's site) or "documents" (its own uploaded
    documents — vula/commerce/profile_from_docs.py); the same rule applies to both: an answer
    is kept only when the sentence it quotes is really in the text."""
    from vula.commerce import business_profile as bp
    open_qs = [q for q in bp.questions(bp._business_type(tenant_id))
               if not (bp.get_answers(tenant_id).get(q["key"]) or "").strip()
               and (only_keys is None or q["key"] in only_keys)]
    chosen = pick_pages(pages)
    if not open_qs or not chosen:
        return {}
    import litellm
    from core.llm_router import resolve_cheap_route
    from core.prompt_safety import fence
    site = "\n\n".join(f"Page: {url}\n# {title or url}\n{text}" for url, title, text in chosen)
    qs = "\n".join(f"- {q['key']}: {q['q']}" for q in open_qs)
    what, text_word, label = _SOURCES.get(source, _SOURCES["website"])
    try:
        model, api_key, api_base = await resolve_cheap_route()
        resp = await litellm.acompletion(
            model=model, api_key=api_key, api_base=api_base, temperature=0, max_tokens=2500,
            messages=[{"role": "system", "content": _PROMPT.format(questions=qs, what=what, text=text_word)},
                      {"role": "user", "content": fence(label, site) + "\n\nJSON:"}])
        body = (resp.choices[0].message.content or "").replace("```json", "").replace("```", "")
        a, b = body.find("{"), body.rfind("}")
        data = json.loads(body[a:b + 1]) if a >= 0 and b > a else {}
    except Exception as exc:
        log.info("profile drafting from the website skipped for %s: %s", tenant_id, exc)
        return {}
    return grounded(data.get("answers") or {}, chosen, {q["key"] for q in open_qs})


# Which profile questions each kind of page can answer (site_facts.topic_of). A FAQ page can
# answer almost anything, so it is asked everything. 12 Nov 2026: the pages used to be read in
# one call of 14,000 characters, home page first — about four pages — so a long terms page or
# FAQ was mostly never read. One call per topic that has pages (at most five).
TOPIC_KEYS = {
    "contact": {"what_we_do", "hours", "area", "contact", "team", "payment", "registration", "principal", "territory"},
    "delivery": {"area", "lead_times", "delivery_fee", "payment"},
    "policies": {"cancellations", "payment", "lead_times", "registration", "bookings"},
    "faq": None,
    "pricing": {"pricing", "delivery_fee", "fees", "payment", "stock", "bookings"},
}


async def draft_by_topic(tenant_id: str, pages: List[Tuple[str, str, str]]) -> Dict[str, Dict[str, str]]:
    """Drafts from website pages, one call per topic, each asking only the questions that kind
    of page can answer. The first topic to answer a question keeps it. Pages that fit no topic
    are read only when no page fits one (the old single call)."""
    from vula.commerce.site_facts import topic_of
    groups: Dict[str, List[Tuple[str, str, str]]] = {}
    for page in pages:
        t = topic_of(page[0], page[1])
        if t:
            groups.setdefault(t, []).append(page)
    if not groups:
        return await draft_from_pages(tenant_id, pages)
    out: Dict[str, Dict[str, str]] = {}
    for topic in TOPIC_KEYS:
        if topic not in groups:
            continue
        keys = TOPIC_KEYS[topic]
        wanted = None if keys is None else keys - set(out)
        if wanted is not None and not wanted:
            continue
        for k, d in (await draft_from_pages(tenant_id, groups[topic], only_keys=wanted)).items():
            out.setdefault(k, d)
    return out


async def draft_and_save(tenant_id: str, pages: List[Tuple[str, str, str]]) -> int:
    """Draft from the imported pages and store the drafts; how many were stored."""
    from vula.commerce import business_profile as bp
    drafts = await draft_by_topic(tenant_id, pages)
    n = bp.save_drafts(tenant_id, drafts) if drafts else 0
    log.info("profile drafts from website for %s: %d kept", tenant_id, n)
    return n
