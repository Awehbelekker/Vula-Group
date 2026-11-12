"""
vula/company_research.py — what the public web says about a company, for the pre-meeting brief.

12 Nov 2026 (Ian): "Vula sees the company name, researches it, and gives a summary brief." Step 3 of
meeting prep. Kept honest and small:

  • companies only — a name that looks like a person's (no company word, no business email
    domain) is never looked up (POPIA: no profiling of private individuals);
  • the search sends only the company's name (and its web domain when we know it) — no customer
    data, no meeting details;
  • the result is the search engine's own title + description + link for the top hits, shown
    as-is and marked "from the web, not checked" — no model writes anything, so nothing is invented;
  • fail-open: no answer within a few seconds → the brief simply goes without it;
  • cached for a week per business and name, so a brief never searches twice for the same company.

DuckDuckGo's HTML endpoint (no API key), the same one core/skills/web_search.py uses.
"""
from __future__ import annotations

import html as _html
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 6.0
CACHE_S = 7 * 24 * 3600
_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, str]]]] = {}

_COMPANY_WORDS = re.compile(
    r"\b(?:pty|ltd|limited|cc|inc|llc|plc|npc|group|holdings|trading|company|co|corp|enterprises?|"
    r"builders?|construction|contractors?|developments?|properties|property|architects?|studio|"
    r"design|engineering|engineers|consulting|consultants|solutions|services|systems|technologies|"
    r"tech|logistics|supplies|suppliers|distributors?|manufacturing|industries|investments|"
    r"partners|associates|agency|clinic|hospital|school|college|university|hotel|restaurant|cafe|"
    r"bakery|foods?|farms?|motors|auto|electrical|plumbing|roofing|tiles|flooring|interiors|"
    r"municipality|department|council|trust|foundation|church|bank|insurance|attorneys|inc\.)\b|&",
    re.IGNORECASE)
_FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.za", "outlook.com", "hotmail.com",
              "live.com", "icloud.com", "me.com", "mweb.co.za", "telkomsa.net", "vodamail.co.za",
              "webmail.co.za", "iafrica.com", "aol.com", "protonmail.com", "proton.me"}
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VulaBrief/1.0)", "Accept-Language": "en-ZA,en;q=0.9"}


def looks_like_company(name: str, domain: Optional[str] = None) -> bool:
    if domain and domain.lower() not in _FREE_MAIL:
        return True
    return bool(_COMPANY_WORDS.search(name or ""))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def parse(page: str, limit: int = 3) -> List[Dict[str, str]]:
    """DuckDuckGo HTML results → [{title, url, snippet}] (ads and DuckDuckGo's own links left out)."""
    out: List[Dict[str, str]] = []
    links = list(re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page or "", re.S))
    for i, m in enumerate(links):
        url = m.group(1)
        um = re.search(r"uddg=([^&]+)", url)
        if um:
            url = unquote(um.group(1))
        if not url.startswith("http") or "duckduckgo.com" in urlparse(url).netloc or "ad_domain" in m.group(0):
            continue
        end = links[i + 1].start() if i + 1 < len(links) else len(page)
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', page[m.end():end], re.S)
        title, snippet = _clean(m.group(2)), _clean(sm.group(1)) if sm else ""
        if title:
            out.append({"title": title[:120], "url": url, "snippet": snippet[:220]})
        if len(out) >= limit:
            break
    return out


async def lookup(tenant_id: str, name: str, domain: Optional[str] = None) -> List[Dict[str, str]]:
    """The top public results for a company, or [] (a person's name, no answer, or nothing found)."""
    name = (name or "").strip()
    if not name or not looks_like_company(name, domain):
        return []
    key = (tenant_id, name.lower())
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    query = f'"{name}"' + (f" site:{domain}" if domain and domain.lower() not in _FREE_MAIL else " South Africa")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        results = parse(resp.text) if resp.status_code == 200 else []
        if not results and domain:                          # nothing on their own site → the wider web
            async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_HEADERS, follow_redirects=True) as client:
                resp = await client.post("https://html.duckduckgo.com/html/", data={"q": f'"{name}" South Africa'})
            results = parse(resp.text) if resp.status_code == 200 else []
    except Exception as exc:
        log.info("company lookup skipped for %r: %s", name, exc)
        return []                                           # a blip isn't cached
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[key] = (time.time(), results)
    return results


def domain_for(tenant_id: str, name: str) -> Optional[str]:
    """Their business email domain, from the mailbox's contacts (vula_email_contacts), if known."""
    from vula.meeting_brief import _client, _key_word
    word = _key_word(name)
    if not word:
        return None
    try:
        rows = (_client().table("vula_email_contacts").select("domain,email,name")
                .eq("tenant_id", tenant_id).ilike("name", f"%{word}%").limit(5).execute().data or [])
    except Exception:
        return None
    for r in rows:
        d = (r.get("domain") or (r.get("email") or "").split("@")[-1] or "").lower()
        if d and d not in _FREE_MAIL:
            return d
    return None


def render(results: List[Dict[str, str]]) -> str:
    if not results:
        return ""
    out = ["🌐 On the web (public results, not checked by Vula):"]
    for r in results:
        out.append(f"• {r['title']}" + (f" — {r['snippet']}" if r.get("snippet") else ""))
        out.append(f"  {r['url']}")
    return "\n".join(out)


async def section(tenant_id: str, name: str) -> str:
    """The brief's web section for this company, or '' — never raises."""
    try:
        return render(await lookup(tenant_id, name, domain_for(tenant_id, name)))
    except Exception as exc:
        log.info("company research skipped: %s", exc)
        return ""
