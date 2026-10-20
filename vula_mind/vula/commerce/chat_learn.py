"""
vula/commerce/chat_learn.py — learn a business's tone and its common answers from its own
WhatsApp chat exports (Ian, 20 Oct 2026: SeedleMe's Matt offered his chat history).

The raw chats are never stored and never put in the searchable knowledge base: they hold other
customers' names, numbers and addresses, and anything in the knowledge base can be quoted to a
customer. Instead, from the uploaded exports:

  - tone: the business's OWN messages (contact details removed) become voice samples, and the
    tone suggestion is re-run — a suggestion the owner accepts or dismisses, never auto-applied
    (vula/commerce/voice_profile.py);
  - answers: customer question → business reply pairs are rewritten as general questions and
    answers with personal details removed, and land as PENDING learned answers for the owner to
    Keep or Bin (Settings › What Vula has learned). Nothing reaches a customer until approved.

Parsing is deterministic; only the question/answer rewrite uses a model.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_FILES = 40
MAX_CHARS = 2_000_000          # per export
MAX_VOICE_SAMPLES = 80
MAX_PAIRS = 80
_BATCH = 20

# Android: "15/10/2026, 14:32 - Matt Wilkinson: text"  (also 2026/10/15, 15.10.26, am/pm)
# iOS:     "[15/10/2026, 14:32:05] Matt Wilkinson: text" (often starts with U+200E)
_LINE_RES = (
    re.compile(r"^‎?(?P<d>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+"
               r"(?P<t>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?\s?[Mm]\.?)?)\s+[-–]\s+"
               r"(?P<s>[^:\n]{1,60}?):\s(?P<m>.*)$"),
    re.compile(r"^‎?\[(?P<d>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+(?P<t>[^\]]{3,20})\]\s"
               r"(?P<s>[^:\n]{1,60}?):\s(?P<m>.*)$"),
)
_SKIP_RE = re.compile(
    r"^‎?\s*(?:<media omitted>|(?:image|video|audio|sticker|document|gif|contact card) omitted|"
    r"this message was deleted|you deleted this message|null|<this message was edited>|"
    r"missed (?:voice|video) call|.*end-to-end encrypted.*|waiting for this message.*)\s*$",
    re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s\-()]{6,}\d)(?!\w)")
_QUESTION_RE = re.compile(
    r"\?|^\s*(?:do|does|can|could|is|are|how|what|when|where|which|who|why|would|will|have)\b",
    re.IGNORECASE)


def _redact(text: str) -> str:
    return _PHONE_RE.sub("[phone]", _EMAIL_RE.sub("[email]", text or ""))


def parse_export(text: str) -> List[Dict[str, str]]:
    """[{sender, text}] in order. Lines that don't start a message continue the previous one."""
    out: List[Dict[str, str]] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        m = next((r.match(line) for r in _LINE_RES if r.match(line)), None)
        if m:
            out.append({"sender": m.group("s").strip().lstrip("‎~ ").strip(),
                        "text": m.group("m").strip()})
        elif out and line.strip():
            out[-1]["text"] += "\n" + line.strip()
    return [m for m in out if m["text"] and not _SKIP_RE.match(m["text"])]


def read_files(files: List[Dict[str, Any]]) -> List[List[Dict[str, str]]]:
    """Each upload → its messages. {name, text} for a .txt, {name, b64} for a .zip (an iPhone
    export, holding _chat.txt) or a .txt sent as base64."""
    chats = []
    for f in (files or [])[:MAX_FILES]:
        text = f.get("text")
        if text is None and f.get("b64"):
            data = base64.b64decode(f["b64"])
            if (f.get("name") or "").lower().endswith(".zip") or data[:2] == b"PK":
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        txt = next((n for n in z.namelist() if n.lower().endswith(".txt")), None)
                        text = z.read(txt).decode("utf-8", "replace") if txt else ""
                except zipfile.BadZipFile:
                    text = ""
            else:
                text = data.decode("utf-8", "replace")
        msgs = parse_export((text or "")[:MAX_CHARS])
        if msgs:
            chats.append(msgs)
    return chats


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def business_sender(chats: List[List[Dict[str, str]]], hints: List[str]) -> Optional[str]:
    """Who the business is in these chats: a sender matching a team member or the business
    name; else, across several chats, the one sender who is in most of them. None when it
    can't tell (a single chat with no matching name) — then the owner picks."""
    senders = Counter()
    for c in chats:
        for s in {m["sender"] for m in c}:
            senders[s] += 1
    want = [_norm(h) for h in hints if _norm(h)]
    for s, _ in senders.most_common():
        n = _norm(s)
        if n and any(n == w or n in w.split() or w in n or n.split()[0] == w.split()[0]
                     for w in want):
            return s
    if len(chats) >= 2:
        top = senders.most_common(2)
        if top and top[0][1] >= 2 and (len(top) == 1 or top[0][1] > top[1][1]):
            return top[0][0]
    return None


def all_senders(chats: List[List[Dict[str, str]]]) -> List[str]:
    c = Counter(m["sender"] for chat in chats for m in chat)
    return [s for s, _ in c.most_common(30)]


def voice_samples(chats: List[List[Dict[str, str]]], me: str) -> List[str]:
    """The business's own messages, contact details removed, short acks and repeats dropped."""
    seen, out = set(), []
    for c in chats:
        for m in c:
            if m["sender"] != me:
                continue
            t = _redact(" ".join(m["text"].split()))
            key = t.lower()
            if len(t.split()) < 3 or key in seen:
                continue
            seen.add(key)
            out.append(t[:400])
    return out[:MAX_VOICE_SAMPLES]


def qa_pairs(chats: List[List[Dict[str, str]]], me: str) -> List[Dict[str, str]]:
    """A customer's question and the business's reply right after it."""
    pairs = []
    for c in chats:
        i = 0
        while i < len(c):
            if c[i]["sender"] == me:
                i += 1
                continue
            j = i
            while j < len(c) and c[j]["sender"] != me:
                j += 1
            asked = [m["text"] for m in c[max(i, j - 2):j]]
            k = j
            while k < len(c) and c[k]["sender"] == me:
                k += 1
            reply = [m["text"] for m in c[j:min(k, j + 3)]]
            q, a = " ".join(" ".join(asked).split()), " ".join(" ".join(reply).split())
            if reply and _QUESTION_RE.search(q) and len(a.split()) >= 4:
                pairs.append({"q": _redact(q)[:400], "a": _redact(a)[:600]})
            i = max(k, i + 1)
    return pairs[:MAX_PAIRS]


_REWRITE = (
    "Below are real WhatsApp exchanges between customers (Q) and a small business (A). Turn the "
    "ones that other customers would ALSO ask into general FAQ entries.\n"
    "Rules:\n"
    "- Keep only general questions: products, sizes, colours, prices, stock, delivery, lead "
    "times, payment, deposits, collection, custom work, care, returns.\n"
    "- Drop anything about one person's own order, payment, address or appointment.\n"
    "- Remove every name, phone number, email, address and order or invoice number.\n"
    "- Use ONLY what the business actually said: keep its wording, figures and tone; add no "
    "facts. Skip pairs where the business didn't really answer (\"let me check\", \"I'll come "
    "back to you\").\n"
    "- Merge duplicates.\n"
    'Return ONLY a JSON array: [{"question": "...", "answer": "..."}]. Return [] if none qualify.'
)


async def rewrite_pairs(pairs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """General Q&A from raw pairs, via the router (cloud when configured, as for other
    rewrites: the local model invents details here)."""
    if not pairs:
        return []
    import litellm
    from core.llm_router import escalate_to_cloud, resolve_generation_route
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="chat_learn")
    esc = escalate_to_cloud("chat_learn", task_type="chat_learn")
    if esc:
        model, api_key, api_base = esc
    out: List[Dict[str, str]] = []
    for n in range(0, len(pairs), _BATCH):
        block = "\n\n".join(f"Q: {p['q']}\nA: {p['a']}" for p in pairs[n:n + _BATCH])
        try:
            resp = await litellm.acompletion(
                model=model, temperature=0, max_tokens=1800, api_key=api_key, api_base=api_base,
                messages=[{"role": "system", "content": _REWRITE}, {"role": "user", "content": block}])
            raw = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S)
            raw = raw[raw.find("["): raw.rfind("]") + 1] if "[" in raw else "[]"
            for item in json.loads(raw or "[]"):
                q, a = str(item.get("question") or "").strip(), str(item.get("answer") or "").strip()
                if q and a:
                    out.append({"question": _redact(q)[:300], "answer": _redact(a)[:800]})
        except Exception as exc:
            log.warning("chat_learn: rewrite batch failed: %s", type(exc).__name__)
    return out


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hints(tenant_id: str) -> List[str]:
    names: List[str] = []
    try:
        from vula.team_index import active_members
        names += [m.get("name") or "" for m in active_members(tenant_id)]
    except Exception:
        pass
    try:
        from vula.api.tenants import get_config
        names.append(get_config(tenant_id).get("display_name") or "")
    except Exception:
        pass
    return [n for n in names if n] + [tenant_id]


def save_voice_samples(tenant_id: str, samples: List[str]) -> int:
    """Into the owner-authored samples voice_profile already reads (the table is named for
    email, its first source; it holds any text the owner wrote themselves)."""
    if not samples:
        return 0
    try:
        existing = {r["excerpt"] for r in (_client().table("vula_email_voice_samples")
                    .select("excerpt").eq("tenant_id", tenant_id).limit(2000).execute().data or [])}
    except Exception:
        existing = set()
    rows = [{"id": str(uuid.uuid4()), "tenant_id": tenant_id, "excerpt": s, "created_at": _now()}
            for s in samples if s not in existing]
    if rows:
        _client().table("vula_email_voice_samples").insert(rows).execute()
    return len(rows)


def save_answers(tenant_id: str, items: List[Dict[str, str]]) -> int:
    """New questions only, as PENDING learned answers (reviewed in Settings before any use)."""
    from vula.escalation import _same_question
    try:
        existing = [r.get("question") or "" for r in (_client().table("vula_learned_answers")
                    .select("question").eq("tenant_id", tenant_id).limit(1000).execute().data or [])]
    except Exception:
        existing = []
    rows = []
    for it in items:
        if any(_same_question(it["question"], q) for q in existing):
            continue
        existing.append(it["question"])
        rows.append({"id": str(uuid.uuid4()), "tenant_id": tenant_id, "question": it["question"],
                     "answer": it["answer"], "source": "chat_import", "status": "pending",
                     "created_at": _now()})
    if rows:
        _client().table("vula_learned_answers").insert(rows).execute()
    return len(rows)


async def learn(tenant_id: str, files: List[Dict[str, Any]], me: Optional[str] = None) -> Dict[str, Any]:
    """The whole pass. Returns counts, or {"need_sender": [...]} when it can't tell which
    side of the chats is the business."""
    chats = read_files(files)
    if not chats:
        return {"error": "Couldn't read any messages — upload WhatsApp exports (.txt or .zip, "
                         "exported 'Without media')."}
    me = me or business_sender(chats, _hints(tenant_id))
    if not me or not any(m["sender"] == me for c in chats for m in c):
        return {"need_sender": all_senders(chats), "chats": len(chats)}
    samples = voice_samples(chats, me)
    pairs = qa_pairs(chats, me)
    items = await rewrite_pairs(pairs)
    saved_samples = save_voice_samples(tenant_id, samples)
    saved_answers = save_answers(tenant_id, items)
    tone = None
    try:
        from vula.commerce import voice_profile
        res = await voice_profile.analyze_voice(tenant_id)
        tone = res.get("suggested")
    except Exception as exc:
        log.warning("chat_learn: tone analysis skipped: %s", type(exc).__name__)
    return {"chats": len(chats), "business": me,
            "messages": sum(len(c) for c in chats),
            "voice_samples": saved_samples, "pairs_found": len(pairs),
            "answers_to_review": saved_answers, "tone_suggested": tone}
