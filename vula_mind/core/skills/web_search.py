"""
core/skills/web_search.py

Real web search skill. Does an actual web search (DuckDuckGo HTML — no API
key), fetches the top results, and synthesises a grounded answer with the
cloud LLM. Falls back to the structured scrapers for tenders/company lookups.

Flow:
  1. SA-tender query  → search etenders + general, list opportunities
  2. otherwise        → DuckDuckGo search → fetch top 3 pages → LLM summarise
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)

_TENDER_RE = re.compile(r"\b(tender|bid|rfp|rfq|procurement|etenders)\b", re.I)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-ZA,en;q=0.9",
}


async def _ddg_search(query: str, limit: int = 5) -> list[dict]:
    """DuckDuckGo HTML search — returns [{title, url, snippet}]. No API key."""
    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
            html = resp.text
        # Parse result blocks
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S
        ):
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # DDG wraps real URL in a redirect — extract uddg param if present
            um = re.search(r"uddg=([^&]+)", url)
            if um:
                from urllib.parse import unquote
                url = unquote(um.group(1))
            if url.startswith("http") and title:
                results.append({"title": title, "url": url})
            if len(results) >= limit:
                break
    except Exception as exc:
        logger.warning("DDG search failed: %s", exc)
    return results


async def _fetch_text(url: str, max_chars: int = 2500) -> str:
    """Fetch a page and return cleaned text."""
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            html = resp.text
        # Strip scripts/styles/tags
        html = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        logger.debug("Fetch failed for %s: %s", url, exc)
        return ""


class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = "Searches the live web (SA tenders, prices, companies, news) and synthesises a grounded answer"

    async def run(self, inp: SkillInput) -> SkillOutput:
        q = inp.question.strip()

        # SA tenders — bias the query to government tender portals
        if _TENDER_RE.search(q):
            search_q = f"{q} site:etenders.gov.za OR South Africa government tender 2026"
        else:
            search_q = f"{q} South Africa" if "south africa" not in q.lower() else q

        # 1. Real search
        hits = await _ddg_search(search_q, limit=5)
        if not hits:
            return SkillOutput(
                answer="I couldn't find live web results for that right now. Try rephrasing.",
                skill_name=self.name, confidence=0.2,
            )

        # 2. Fetch the top 3 pages for grounding
        contexts = []
        sources = []
        for h in hits[:3]:
            text = await _fetch_text(h["url"])
            if text:
                domain = urlparse(h["url"]).netloc
                contexts.append(f"[{domain}] {h['title']}\n{text}")
                sources.append({"type": "web", "title": h["title"], "url": h["url"]})
        # Always include the result list as a fallback source
        for h in hits:
            if not any(s["url"] == h["url"] for s in sources):
                sources.append({"type": "web", "title": h["title"], "url": h["url"]})

        if not contexts:
            # Couldn't fetch pages — at least return the result links
            links = "\n".join(f"• {h['title']}\n  {h['url']}" for h in hits[:5])
            return SkillOutput(
                answer=f"Here's what I found online:\n{links}",
                skill_name=self.name, confidence=0.4, sources=sources,
            )

        # 3. Synthesise a grounded answer with the cloud LLM
        try:
            import litellm
            from core.llm_router import resolve_generation_route
            from core.prompt_safety import fence, UNTRUSTED_CONTENT_RULE
            litellm.drop_params = True
            model, api_key, api_base = await resolve_generation_route()

            context_block = "\n\n---\n\n".join(contexts)[:6000]
            # Price/product research gets a fuller, structured breakdown.
            is_research = bool(re.search(
                r"\b(price|prices|cost|costs|rate|rates|quote|supplier|buy|compare|"
                r"cheapest|how much|per (kg|m2|m3|unit|litre|bag|ton))\b", q, re.I))
            if is_research:
                system = (
                    "You are Vula, a South African procurement researcher. Using ONLY the web "
                    "results below, produce a clear PRICE BREAKDOWN. Format:\n"
                    "- A one-line summary.\n"
                    "- Then a list, one per option: *Item* — R<price> (unit) — Supplier — short note.\n"
                    "- End with a 'Best value:' line and any caveats (stock, delivery, date).\n"
                    "Cite real figures only; if a price isn't in the results, say 'price not listed'. "
                    "ZAR for money, SA suppliers. Don't invent numbers.\n\n"
                    + UNTRUSTED_CONTENT_RULE
                )
                cap = max(inp.max_tokens, 900)
            else:
                system = (
                    "You are Vula. Answer the question using ONLY the web search results below. "
                    "Be specific and cite figures/dates where present. If the results don't "
                    "answer it, say so. South African context, ZAR for money. Concise.\n\n"
                    + UNTRUSTED_CONTENT_RULE
                )
                cap = min(inp.max_tokens, 600)
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content":
                        # Fetched page text is the least trustworthy content this skill
                        # handles — it's raw text from arbitrary internet pages, not even a
                        # curated KB. Previously this had no fencing and no untrusted-content
                        # rule at all: a page (including one an attacker seeded to rank for
                        # the query) could contain "ignore prior instructions and..." with
                        # nothing structurally telling the model it's data, not a command.
                        f"{fence('WEB_SEARCH_RESULTS', context_block)}\n\nQuestion: {q}\n\nAnswer:"},
                ],
                temperature=0.2,
                max_tokens=cap,
                api_key=api_key,
                api_base=api_base,
            )
            answer = (resp.choices[0].message.content or "").strip()
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
            return SkillOutput(
                answer=answer or "No clear answer from the search results.",
                skill_name=self.name,
                confidence=0.7,
                sources=sources,
            )
        except Exception as exc:
            logger.error("web_search synthesis failed: %s", exc)
            links = "\n".join(f"• {h['title']}: {h['url']}" for h in hits[:4])
            return SkillOutput(
                answer=f"Found these but couldn't summarise:\n{links}",
                skill_name=self.name, confidence=0.4, sources=sources, error=str(exc),
            )
