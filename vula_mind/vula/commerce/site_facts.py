"""What a website says about the business, read exactly — for the website import (site_import.py).

12 Nov 2026 (Ian: "how strong is our website scrape … everything from products, images and
descriptions to terms and privacy"). The import read every page whole (menu, footer and cookie
banner included, on every page), left the address, phone and opening hours for a model to find,
and only ever looked at /sitemap.xml. These are the pure pieces — no network, no database — that
fix that:

  main_text        a page's own content, without the menu, header, footer and banners
  footer_text      the footer, read once: where the address, phone and hours usually are
  links            same-site links on a page (to crawl a site that has no sitemap), footer ones first
  pdf_links        linked PDFs worth reading: menus, price lists, brochures, terms
  business_facts   phone, email, address, opening hours, socials — from the page's schema.org data
                   and tel:/mailto: links, each with the exact text it came from
  facts_as_drafts  those facts as business-profile drafts, grounded by construction
  builds_in_browser  a page that is all script and no text (the owner is told why little was found)
  topic_of         which group of profile questions a page can answer
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# ── a page's own content ─────────────────────────────────────────────────────────────────────

_DROP_BLOCKS = re.compile(
    r"(?is)<(script|style|noscript|svg|template|iframe|form)\b[^>]*>.*?</\1>"
    r"|<(nav|header|footer|aside)\b[^>]*>.*?</\2>")
# cookie / newsletter / popup blocks, by their class or id
_DROP_BY_NAME = re.compile(
    r'(?is)<(div|section)\b[^>]*(?:class|id)="[^"]*(?:cookie|consent|gdpr|newsletter|popup|modal|'
    r'announcement-bar|mobile-menu|site-nav|breadcrumb)[^"]*"[^>]*>.*?</\1>')
_MAIN = re.compile(r'(?is)<(main)\b[^>]*>(.*?)</main>|<(article)\b[^>]*>(.*?)</article>'
                   r'|<(div|section)\b[^>]*role="main"[^>]*>(.*)')
_FOOTER = re.compile(r"(?is)<footer\b[^>]*>(.*?)</footer>")


def _to_text(fragment: str) -> str:
    from vula.commerce.reference_url import _extract_title_and_text
    _, text = _extract_title_and_text(fragment or "")
    text = _html.unescape(text or "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)               # leftover image markdown
    text = re.sub(r"\[([^\]]*)\]\((?:[^)]*)\)", r"\1", text)        # [label](url) → label
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def main_text(page: str) -> str:
    """The page's own content. <main>/<article>/[role=main] when the page marks it; otherwise
    the page with its navigation, header, footer, sidebars, forms and banners taken out."""
    page = page or ""
    m = _MAIN.search(page)
    if m:
        inner = next(g for g in (m.group(2), m.group(4), m.group(6)) if g is not None)
        inner = _DROP_BY_NAME.sub(" ", _DROP_BLOCKS.sub(" ", inner))
        text = _to_text(inner)
        if len(text) >= 80:
            return text
    body = re.search(r"(?is)<body\b[^>]*>(.*)</body>", page)
    rest = _DROP_BY_NAME.sub(" ", _DROP_BLOCKS.sub(" ", body.group(1) if body else page))
    return _to_text(rest)


def footer_text(page: str) -> str:
    """The page's footer as text ("" when it has none)."""
    m = _FOOTER.search(page or "")
    if not m:
        return ""
    return _to_text(re.sub(r"(?is)<(script|style|svg|form)\b[^>]*>.*?</\1>", " ", m.group(1)))


# ── links ────────────────────────────────────────────────────────────────────────────────────

_HREF = re.compile(r'(?is)<a\b[^>]*\bhref\s*=\s*["\']([^"\'#]+)["\'][^>]*>(.*?)</a>')
_FILE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|ico|css|js|zip|mp4|mp3|mov|xml|json|docx?|xlsx?)$", re.I)


def _same_site(url: str, root: str) -> bool:
    host = urlparse(url).netloc.lower()
    base = urlparse(root).netloc.lower()
    return host == base or host == "www." + base or "www." + host == base


def _clean_url(href: str, page_url: str) -> str:
    u = urljoin(page_url, _html.unescape(href.strip()))
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}{p.path or '/'}" + (f"?{p.query}" if p.query else "")


def links(page: str, page_url: str, root: str) -> List[str]:
    """Same-site page links on this page, in the order a reader would find the important ones:
    footer links first (terms, privacy, contact live there), then the rest. No files, no
    anchors, no mail or phone links."""
    page = page or ""
    footer = _FOOTER.search(page)
    parts = ([footer.group(1)] if footer else []) + [page]
    out: List[str] = []
    for part in parts:
        for href, _label in _HREF.findall(part):
            if href.lower().startswith(("mailto:", "tel:", "javascript:", "whatsapp:", "data:")):
                continue
            u = _clean_url(href, page_url)
            if not u.startswith(("http://", "https://")) or not _same_site(u, root):
                continue
            if _FILE_EXT.search(urlparse(u).path) or urlparse(u).path.lower().endswith(".pdf"):
                continue
            if u not in out:
                out.append(u)
    return out


_PDF_WORTH = re.compile(r"menu|price|pricing|tariff|rate|brochure|catalog|terms|conditions|policy|"
                        r"profile|services|faq|delivery|returns|warranty", re.I)


def pdf_links(page: str, page_url: str, root: str) -> List[Tuple[str, str]]:
    """(url, label) for PDFs on this site worth reading: menus, price lists, brochures, terms,
    company profiles — judged by the file name or the link's words."""
    out: List[Tuple[str, str]] = []
    for href, label in _HREF.findall(page or ""):
        u = _clean_url(href, page_url)
        if not urlparse(u).path.lower().endswith(".pdf") or not _same_site(u, root):
            continue
        words = re.sub(r"<[^>]+>", " ", label or "")
        name = urlparse(u).path.rsplit("/", 1)[-1]
        if (_PDF_WORTH.search(name) or _PDF_WORTH.search(words)) and u not in [x[0] for x in out]:
            out.append((u, (_html.unescape(words).strip() or name)[:120]))
    return out


# ── exact facts ──────────────────────────────────────────────────────────────────────────────

_BUSINESS_TYPES = ("Organization", "LocalBusiness", "Store", "Restaurant", "FoodEstablishment",
                   "ProfessionalService", "HomeAndConstructionBusiness", "MedicalBusiness",
                   "HealthAndBeautyBusiness", "AutomotiveBusiness", "LodgingBusiness", "Corporation",
                   "Bakery", "CafeOrCoffeeShop", "ClothingStore", "Dentist", "HairSalon",
                   "BeautySalon", "Electrician", "Plumber", "GeneralContractor", "RealEstateAgent")
_DAYS = {"mo": "Monday", "tu": "Tuesday", "we": "Wednesday", "th": "Thursday", "fr": "Friday",
         "sa": "Saturday", "su": "Sunday"}
_TEL = re.compile(r'(?i)href\s*=\s*["\']tel:([^"\']+)["\']')
_MAIL = re.compile(r'(?i)href\s*=\s*["\']mailto:([^"\'?]+)')
_SOCIAL = re.compile(r"(?i)https?://(?:www\.)?(facebook|instagram|linkedin|tiktok|youtube|x|twitter)\.com/[^\s\"'<>]+")


def _jsonld_items(page: str) -> Iterable[Dict[str, Any]]:
    for block in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if not isinstance(d, dict):
                continue
            yield d
            graph = d.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)


def _is_business(d: Dict[str, Any]) -> bool:
    t = d.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(str(x) in _BUSINESS_TYPES or str(x).endswith(("Business", "Store", "Service")) for x in types)


def _address(a: Any) -> str:
    if isinstance(a, str):
        return a.strip()
    if not isinstance(a, dict):
        return ""
    parts = [a.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _day_names(days: Any) -> List[str]:
    out = []
    for d in (days if isinstance(days, list) else [days]):
        name = str(d or "").rsplit("/", 1)[-1].strip()
        if name:
            out.append(name)
    return out


def _hours(d: Dict[str, Any]) -> str:
    """Opening hours as one readable line, from openingHoursSpecification or openingHours."""
    specs = d.get("openingHoursSpecification")
    lines: List[str] = []
    for s in (specs if isinstance(specs, list) else [specs] if specs else []):
        if not isinstance(s, dict):
            continue
        days = _day_names(s.get("dayOfWeek"))
        o, c = str(s.get("opens") or "")[:5], str(s.get("closes") or "")[:5]
        if days and o and c:
            lines.append(f"{', '.join(days)} {o}–{c}")
    if not lines:
        raw = d.get("openingHours")
        for h in (raw if isinstance(raw, list) else [raw] if raw else []):
            m = re.match(r"^\s*([A-Za-z,\- ]+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", str(h))
            if not m:
                continue
            days = re.sub(r"[A-Za-z]{2}", lambda x: _DAYS.get(x.group(0).lower(), x.group(0)), m.group(1).strip())
            lines.append(f"{days} {m.group(2)}–{m.group(3)}")
    return "; ".join(lines)


def business_facts(pages: List[Tuple[str, str]]) -> Dict[str, Dict[str, str]]:
    """{fact: {"value", "url"}} from (url, raw html) pages: name, phone, email, address, hours,
    area, price_range, socials. Each value is exactly what the page states; the first page that
    states a fact wins. Nothing is inferred."""
    out: Dict[str, Dict[str, str]] = {}

    def put(key: str, value: Any, url: str) -> None:
        v = re.sub(r"\s+", " ", str(value or "")).strip()
        if v and key not in out:
            out[key] = {"value": v[:400], "url": url}

    for url, page in pages:
        for d in _jsonld_items(page):
            if not _is_business(d):
                continue
            put("name", d.get("name"), url)
            put("phone", d.get("telephone"), url)
            put("email", str(d.get("email") or "").replace("mailto:", ""), url)
            put("address", _address(d.get("address")), url)
            put("hours", _hours(d), url)
            area = d.get("areaServed")
            names = [a.get("name") if isinstance(a, dict) else a for a in (area if isinstance(area, list) else [area] if area else [])]
            put("area", ", ".join(str(n) for n in names if n), url)
            put("price_range", d.get("priceRange"), url)
            same = d.get("sameAs")
            put("socials", ", ".join(s for s in (same if isinstance(same, list) else [same] if same else []) if isinstance(s, str)), url)
        tel = _TEL.search(page or "")
        if tel:
            put("phone", _html.unescape(tel.group(1)), url)
        mail = _MAIL.search(page or "")
        if mail:
            put("email", _html.unescape(mail.group(1)), url)
        socials = sorted(set(m.group(0).rstrip("/") for m in _SOCIAL.finditer(page or "")))
        if socials:
            put("socials", ", ".join(socials[:6]), url)
    return out


def facts_as_drafts(facts: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Business-profile drafts from exact facts: hours and area as they are; phone, email and
    address together as the contact answer. The quote is the stated value itself, so the draft
    is grounded by construction."""
    drafts: Dict[str, Dict[str, str]] = {}
    if facts.get("hours"):
        f = facts["hours"]
        drafts["hours"] = {"answer": f["value"], "quote": f["value"], "url": f["url"]}
    if facts.get("area"):
        f = facts["area"]
        drafts["area"] = {"answer": f["value"], "quote": f["value"], "url": f["url"]}
    contact = [(k, facts[k]) for k in ("phone", "email", "address") if facts.get(k)]
    if contact:
        label = {"phone": "Phone", "email": "Email", "address": "Address"}
        drafts["contact"] = {"answer": "; ".join(f"{label[k]}: {f['value']}" for k, f in contact),
                             "quote": contact[0][1]["value"], "url": contact[0][1]["url"]}
    return drafts


# ── what kind of page ────────────────────────────────────────────────────────────────────────

def builds_in_browser(page: str, text: str) -> bool:
    """A page that is almost all script and almost no text: the site builds its pages in the
    browser, so a plain read finds little. Used to tell the owner why."""
    scripts = sum(len(s) for s in re.findall(r"(?is)<script\b[^>]*>.*?</script>", page or ""))
    return len(text or "") < 200 and (scripts > 20_000 or bool(re.search(
        r'(?i)<div[^>]+id="(?:root|app|__next|__nuxt)"[^>]*>\s*</div>', page or "")))


TOPICS: Dict[str, re.Pattern] = {
    "contact": re.compile(r"contact|about|find[-_ ]?us|location|hours|team|who[-_ ]we", re.I),
    "delivery": re.compile(r"deliver|shipping|collect|courier|areas?", re.I),
    "policies": re.compile(r"returns?|refund|terms|conditions|privacy|policy|policies|legal|warrant|"
                           r"guarantee|cancel", re.I),
    "faq": re.compile(r"faq|questions|help|support|how[-_ ]it[-_ ]works", re.I),
    "pricing": re.compile(r"pric|rates?|tariff|menu|services|packages|fees", re.I),
}
POLICY_KINDS: Dict[str, re.Pattern] = {
    "terms": re.compile(r"terms|conditions|t-?and-?c|tos\b", re.I),
    "privacy": re.compile(r"privacy|popia|data[-_ ]protection|paia", re.I),
    "returns": re.compile(r"returns?|refund|exchange", re.I),
    "shipping": re.compile(r"shipping|deliver", re.I),
}


def topic_of(url: str, title: str = "") -> Optional[str]:
    """The profile-question group this page belongs to, from its address and title; the home
    page counts as contact. None for pages that answer none (blog posts, product lists)."""
    path = urlparse(url).path or "/"
    if path in ("", "/"):
        return "contact"
    where = f"{path} {title or ''}"
    return next((t for t, rx in TOPICS.items() if rx.search(where)), None)


def policy_kind(url: str, title: str = "") -> Optional[str]:
    where = f"{urlparse(url).path} {title or ''}"
    return next((k for k, rx in POLICY_KINDS.items() if rx.search(where)), None)
