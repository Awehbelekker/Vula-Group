"""
tools/staging_flows.py — real WhatsApp conversations against local staging, with nothing sent.

3 Nov 2026 (docs/staging.md, "Local staging"). evals/golive.py checks what the AI answers; this
checks everything around it, end to end: Meta's signed webhook → routing → the reply Vula would
send. A stand-in Meta runs on this machine (GRAPH_HOST, honoured only for a loopback address)
and records every message, so the checks read exactly what a customer or owner would receive.

It creates one test business, "staging-flows" (owner +27 82 000 0001, number id STAGINGPNID),
in the local database, and only ever runs against a database whose URL is local.

    # 1. local Supabase + migrations (docs/staging.md, section 0)
    # 2. the backend, pointed at it and at the stand-in Meta:
    export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
    python tools/staging_flows.py seed
    GRAPH_HOST=http://127.0.0.1:8799 VULA_FB_APP_SECRET=staging-flows-secret \
      WHATSAPP_TOKEN=staging-token WHATSAPP_PHONE_ID=STAGINGPNID VULA_ENV=staging \
      SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_KEY=<local service key> \
      uvicorn vula.api.server:app --port 7438
    # 3. the flows:
    python tools/staging_flows.py run --base-url http://127.0.0.1:7438

Exit code 0 when every flow passes.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

TENANT = "staging-flows"
PNID = "STAGINGPNID"
OWNER = "27820000001"
CUSTOMER = "27830000001"
CUSTOMER_2 = "27830000002"
APP_SECRET_DEFAULT = "staging-flows-secret"
META_PORT = 8799


# ── Stand-in Meta ──────────────────────────────────────────────────────────────────────────

@dataclass
class Sent:
    to: str
    kind: str
    text: str
    buttons: List[Dict[str, str]] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class FakeMeta:
    """Records POST …/messages; serves media registered with add_media."""

    def __init__(self, port: int = META_PORT):
        self.sent: List[Sent] = []
        self.media: Dict[str, tuple] = {}
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    payload = {}
                if self.path.rstrip("/").endswith("/messages"):
                    outer._record(payload)
                    return self._json({"messages": [{"id": f"wamid.fake.{uuid.uuid4().hex[:12]}"}]})
                return self._json({"success": True})

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/media/"):
                    mid = path.rsplit("/", 1)[-1]
                    if mid in outer.media:
                        data, mime = outer.media[mid]
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._json({"error": "no such media"}, 404)
                mid = path.rsplit("/", 1)[-1]
                if mid in outer.media:
                    data, mime = outer.media[mid]
                    return self._json({"url": f"http://127.0.0.1:{port}/media/{mid}",
                                       "mime_type": mime, "file_size": len(data), "id": mid})
                return self._json({"data": []})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()

    def add_media(self, media_id: str, data: bytes, mime: str):
        self.media[media_id] = (data, mime)

    def _record(self, p: dict):
        kind = p.get("type", "")
        text, buttons = "", []
        if kind == "text":
            text = (p.get("text") or {}).get("body", "")
        elif kind == "interactive":
            inter = p.get("interactive") or {}
            text = ((inter.get("body") or {}).get("text") or "")
            for b in ((inter.get("action") or {}).get("buttons") or []):
                r = b.get("reply") or {}
                buttons.append({"id": r.get("id", ""), "title": r.get("title", "")})
        elif kind == "template":
            text = json.dumps(p.get("template") or {})
        with self.lock:
            self.sent.append(Sent(to=str(p.get("to", "")), kind=kind, text=text, buttons=buttons, raw=p))

    def to(self, number: str) -> List[Sent]:
        with self.lock:
            return [s for s in self.sent if s.to.lstrip("+") == number]

    def wait_for(self, number: str, pred: Callable[[Sent], bool], timeout: float = 60.0) -> Optional[Sent]:
        end = time.time() + timeout
        while time.time() < end:
            for s in self.to(number):
                if pred(s):
                    return s
            time.sleep(0.25)
        return None


# ── Webhooks ───────────────────────────────────────────────────────────────────────────────

def _envelope(msg: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA-STAGING", "changes": [{
        "field": "messages",
        "value": {"messaging_product": "whatsapp",
                  "metadata": {"display_phone_number": "27210000000", "phone_number_id": PNID},
                  "contacts": [{"profile": {"name": "Staging"}, "wa_id": msg["from"]}],
                  "messages": [msg]}}]}]}


def text_msg(sender: str, body: str, msg_id: Optional[str] = None) -> dict:
    return {"from": sender, "id": msg_id or f"wamid.in.{uuid.uuid4().hex[:16]}",
            "timestamp": str(int(time.time())), "type": "text", "text": {"body": body}}


def document_msg(sender: str, media_id: str, filename: str, mime: str, caption: str = "") -> dict:
    doc = {"id": media_id, "filename": filename, "mime_type": mime}
    if caption:
        doc["caption"] = caption
    return {"from": sender, "id": f"wamid.in.{uuid.uuid4().hex[:16]}",
            "timestamp": str(int(time.time())), "type": "document", "document": doc}


def button_msg(sender: str, button_id: str, title: str) -> dict:
    return {"from": sender, "id": f"wamid.in.{uuid.uuid4().hex[:16]}",
            "timestamp": str(int(time.time())), "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": title}}}


def post_webhook(base_url: str, secret: str, msg: dict, sign: bool = True) -> int:
    import httpx
    raw = json.dumps(_envelope(msg)).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    r = httpx.post(f"{base_url.rstrip('/')}/v1/whatsapp/webhook", content=raw, headers=headers, timeout=30)
    return r.status_code


# ── Seed ───────────────────────────────────────────────────────────────────────────────────

def _local_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    host = urlsplit(dsn).hostname if dsn else None
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("DATABASE_URL must be a local database (127.0.0.1 / localhost). Refusing.")
    return dsn


SEED_SQL = [
    ("insert into vula_tenants (tenant_id, company_name, workspace_slug, status, plan) "
     "values (%s, 'Staging Flows Builders', %s, 'active', 'business') on conflict (tenant_id) do nothing",
     (TENANT, TENANT)),
    ("insert into vula_tenant_config (tenant_id, display_name, business_type, status, modules) "
     "values (%s, 'Staging Flows Builders', 'construction', 'active', %s::jsonb) "
     "on conflict (tenant_id) do update set modules = excluded.modules, status = 'active'",
     (TENANT, json.dumps(["estimating", "projects", "invoices", "documents"]))),
    ("delete from vula_whatsapp_accounts where phone_number_id = %s", (PNID,)),
    ("insert into vula_whatsapp_accounts (tenant_id, phone_number_id, phone_number, status, "
     "route_mode, access_token, is_primary, verified_name) values (%s, %s, '+27210000000', "
     "'connected', 'commerce', 'staging-token', true, 'Staging Flows')", (TENANT, PNID)),
    ("delete from vula_team_members where tenant_id = %s", (TENANT,)),
    ("insert into vula_team_members (tenant_id, name, whatsapp, role, active) "
     "values (%s, 'Test Owner', %s, 'owner', true)", (TENANT, "+" + OWNER)),
]


def seed() -> None:
    import psycopg
    with psycopg.connect(_local_dsn()) as conn, conn.cursor() as cur:
        for sql, args in SEED_SQL:
            cur.execute(sql, args)
        conn.commit()
    print(f"seeded {TENANT}: number {PNID}, owner +{OWNER}")


# ── Flows ──────────────────────────────────────────────────────────────────────────────────

def _dxf_bytes() -> bytes:
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4                         # millimetres
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (12000, 0), (12000, 8000), (0, 8000)], close=True)   # outline
    msp.add_lwpolyline([(0, 0), (5000, 0), (5000, 4000), (0, 4000)], close=True)
    msp.add_text("LOUNGE", dxfattribs={"insert": (2000, 2000), "height": 200})
    msp.add_lwpolyline([(5000, 0), (9000, 0), (9000, 4000), (5000, 4000)], close=True)
    msp.add_text("KITCHEN", dxfattribs={"insert": (7000, 2000), "height": 200})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


def run_flows(base_url: str, secret: str, meta: FakeMeta, only: Optional[List[str]] = None) -> List[Result]:
    results: List[Result] = []

    def flow(name):
        def deco(fn):
            if only and name not in only:
                return fn
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"crashed: {type(exc).__name__}: {exc}"
            results.append(Result(name, ok, detail))
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
            return fn
        return deco

    @flow("unsigned webhook refused")
    def _():
        code = post_webhook(base_url, secret, text_msg(CUSTOMER_2, "hello"), sign=False)
        return code == 403, f"HTTP {code}"

    @flow("crisis: customer gets the helplines, owner is alerted")
    def _():
        before = len(meta.to(OWNER))
        post_webhook(base_url, secret, text_msg(CUSTOMER, "I want to end my life"))
        reply = meta.wait_for(CUSTOMER, lambda s: "0800 567 567" in s.text, 30)
        alert = meta.wait_for(OWNER, lambda s: "at risk" in s.text, 20)
        others = [s for s in meta.to(CUSTOMER) if "0800 567 567" not in s.text]
        leaked = alert and "end my life" in alert.text
        ok = bool(reply) and bool(alert) and not others and not leaked and len(meta.to(OWNER)) > before
        return ok, ("helplines sent; owner alerted without the message" if ok else
                    f"reply={bool(reply)} alert={bool(alert)} extra_replies={len(others)} leaked={bool(leaked)}")

    @flow("abuse: GBV line sent, business not told")
    def _():
        owner_before = len(meta.to(OWNER))
        post_webhook(base_url, secret, text_msg(CUSTOMER_2, "my boyfriend hits me"))
        reply = meta.wait_for(CUSTOMER_2, lambda s: "0800 428 428" in s.text, 30)
        time.sleep(3)
        told = len(meta.to(OWNER)) - owner_before
        return bool(reply) and told == 0, f"reply={bool(reply)} owner_messages={told}"

    @flow("duplicate delivery answered once")
    def _():
        mid = f"wamid.in.dup.{uuid.uuid4().hex[:10]}"
        before = len(meta.to(CUSTOMER_2))
        for _ in range(2):
            post_webhook(base_url, secret, text_msg(CUSTOMER_2, "I am being abused", msg_id=mid))
        meta.wait_for(CUSTOMER_2, lambda s: "0800 428 428" in s.text and meta.to(CUSTOMER_2).index(s) >= before, 30)
        time.sleep(4)
        n = len([s for s in meta.to(CUSTOMER_2)[before:] if "0800 428 428" in s.text])
        return n == 1, f"{n} replies to one message delivered twice"

    @flow("takeoff on WhatsApp: plan → measured rooms → Price it → priced")
    def _():
        media_id = f"media-{uuid.uuid4().hex[:8]}"
        meta.add_media(media_id, _dxf_bytes(), "application/octet-stream")
        before = len(meta.to(OWNER))
        post_webhook(base_url, secret, document_msg(OWNER, media_id, "house.dxf", "application/octet-stream",
                                                    caption="takeoff please"))
        ack = meta.wait_for(OWNER, lambda s: "Reading your plans" in s.text, 60)
        preview = meta.wait_for(OWNER, lambda s: bool(s.buttons) and "m²" in s.text, 120)
        if not preview:
            got = [s.text[:120] for s in meta.to(OWNER)[before:]]
            return False, f"ack={bool(ack)}; no preview with buttons. Owner got: {got}"
        if "LOUNGE".lower() not in preview.text.lower() or "KITCHEN".lower() not in preview.text.lower():
            return False, f"preview doesn't name the rooms: {preview.text[:200]}"
        price = next((b for b in preview.buttons if "price" in b["title"].lower()), None)
        if not price:
            return False, f"no Price it button: {preview.buttons}"
        post_webhook(base_url, secret, button_msg(OWNER, price["id"], price["title"]))
        priced = meta.wait_for(OWNER, lambda s: "Priced" in s.text or "pricing failed" in s.text.lower(), 120)
        if not priced:
            got = [s.text[:120] for s in meta.to(OWNER)[before:]]
            return False, f"nothing after Price it. Owner got: {got}"
        return "Priced" in priced.text, priced.text.splitlines()[0][:160]

    @flow("STOP: customer is told, and the opt-out is on record (POPIA)")
    def _():
        who = "2784" + uuid.uuid4().hex[:7].translate(str.maketrans("abcdef", "123456"))
        post_webhook(base_url, secret, text_msg(who, "STOP"))
        reply = meta.wait_for(who, lambda s: bool(s.text.strip()), 30)
        status = None
        dsn = os.environ.get("DATABASE_URL", "")
        if dsn:
            import psycopg
            with psycopg.connect(_local_dsn()) as conn, conn.cursor() as cur:
                cur.execute("select status from commerce_consent where tenant_id=%s and "
                            "right(regexp_replace(phone, '\\D', '', 'g'), 9) = right(%s, 9)", (TENANT, who))
                row = cur.fetchone()
                status = row[0] if row else None
        ok = bool(reply) and (status == "opted_out" if dsn else True)
        return ok, f"reply={reply.text[:70]!r} recorded={status}" if reply else f"no reply; recorded={status}"

    @flow("customer still gets an answer when the AI is down")
    def _():
        who = "2783" + str(int(time.time()) % 10_000_000).zfill(7)
        post_webhook(base_url, secret, text_msg(who, "Hi, what are your opening hours?"))
        reply = meta.wait_for(who, lambda s: bool(s.text.strip()), 120)
        return bool(reply), (f"replied: {reply.text[:120]!r}" if reply else "no reply within 120s (silence)")

    return results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    r = sub.add_parser("run")
    r.add_argument("--base-url", default="http://127.0.0.1:7438")
    r.add_argument("--secret", default=os.environ.get("VULA_FB_APP_SECRET", APP_SECRET_DEFAULT))
    r.add_argument("--only", action="append", help="run just this flow (repeatable)")
    args = ap.parse_args(argv)
    if args.cmd == "seed":
        seed()
        return 0
    meta = FakeMeta().start()
    try:
        results = run_flows(args.base_url, args.secret, meta, args.only)
    finally:
        meta.stop()
    passed = sum(r.ok for r in results)
    print(f"\n{passed}/{len(results)} flows passed")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
