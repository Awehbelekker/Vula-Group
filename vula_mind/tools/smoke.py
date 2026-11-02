"""
tools/smoke.py — a quick outside-in check of a deployed Vula API (staging first, then production).

Each check is a plain HTTP request that needs no tenant data and changes nothing:

  /status                         200 (public health check)
  /ready                          200 or 503 with the API key (the gap list is summarised);
                                  401 without one
  GET  /v1/whatsapp/webhook       403 for a wrong verify token
  POST /v1/whatsapp/webhook       403 when unsigned (X-Hub-Signature-256 missing)
  /v1/commerce/<x>/admin/orders   401 with no credentials (fails if ENFORCE_TENANT_AUTH is off)
  /takeoff/rates                  401 with no credentials

Run from vula_mind/:

    python tools/smoke.py --base-url https://<staging-service>.up.railway.app --api-key-env STAGING_API_KEY

Prints a table and exits 1 if any check failed. The API key is read from the named environment
variable and never printed.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

# Not a real tenant: the guard must refuse before any handler looks it up.
SMOKE_TENANT = "smoke-check-nonexistent"


@dataclass
class Result:
    name: str
    ok: bool
    status: Optional[int]
    expected: str
    detail: str = ""


def _ready_summary(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return "body is not JSON"
    platform = body.get("platform") or []
    tenants = body.get("tenants") or {}
    head = "ready" if body.get("ready") else f"NOT READY: {len(platform)} platform gap(s)"
    shown = "; ".join(str(g.get("what", "")) for g in platform[:3])
    tail = f"; {len(tenants)} tenant(s) with gaps" if tenants else ""
    return f"{head}{(': ' + shown) if shown else ''}{tail}"


def _check(client: httpx.Client, name: str, method: str, path: str, expect: tuple[int, ...],
           summarise: Optional[Callable[[httpx.Response], str]] = None, **kw) -> Result:
    want = " or ".join(map(str, expect))
    try:
        resp = client.request(method, path, **kw)
    except httpx.HTTPError as exc:
        return Result(name, False, None, want, f"request failed: {type(exc).__name__}")
    ok = resp.status_code in expect
    detail = summarise(resp) if (summarise and ok) else ("" if ok else resp.text[:120].replace("\n", " "))
    return Result(name, ok, resp.status_code, want, detail)


def run_checks(client: httpx.Client, api_key: str = "") -> list[Result]:
    results = [_check(client, "health check", "GET", "/status", (200,),
                      summarise=lambda r: str(r.json().get("status", "")) if r.headers.get(
                          "content-type", "").startswith("application/json") else "")]
    if api_key:
        results.append(_check(client, "readiness (with API key)", "GET", "/ready", (200, 503),
                              summarise=_ready_summary, headers={"X-API-Key": api_key}))
    else:
        results.append(_check(client, "readiness needs the API key", "GET", "/ready", (401,),
                              summarise=lambda r: "pass --api-key-env to read the gap list"))
    wrong = "smoke-" + secrets.token_hex(8)
    results.append(_check(client, "webhook verify, wrong token", "GET", "/v1/whatsapp/webhook", (403,),
                          params={"hub.mode": "subscribe", "hub.verify_token": wrong,
                                  "hub.challenge": "x"}))
    results.append(_check(client, "webhook POST, unsigned", "POST", "/v1/whatsapp/webhook", (403,),
                          json={"object": "whatsapp_business_account", "entry": []}))
    results.append(_check(client, "tenant admin route, no auth", "GET",
                          f"/v1/commerce/{SMOKE_TENANT}/admin/orders", (401,)))
    results.append(_check(client, "takeoff rates, no auth", "GET", "/takeoff/rates", (401,)))
    return results


def render(results: list[Result]) -> str:
    rows = [("", "check", "status", "expected", "detail")]
    rows += [("PASS" if r.ok else "FAIL", r.name, str(r.status) if r.status is not None else "-",
              r.expected, r.detail) for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(row[:4])) + "  " + row[4]
             for row in rows]
    failed = sum(not r.ok for r in results)
    lines.append(f"\n{len(results) - failed}/{len(results)} passed" + (f", {failed} FAILED" if failed else ""))
    return "\n".join(line.rstrip() for line in lines)


def main(argv: Optional[list[str]] = None, *, transport: Optional[httpx.BaseTransport] = None,
         env: Optional[dict] = None, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(prog="smoke.py", description="Smoke-check a deployed Vula API.")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key-env", default="", help="env var holding the API key (for /ready)")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    api_key = env.get(args.api_key_env, "") if args.api_key_env else ""
    if args.api_key_env and not api_key:
        out(f"note: {args.api_key_env} is empty; /ready will be checked as auth-protected only")
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout,
                      transport=transport, follow_redirects=False) as client:
        results = run_checks(client, api_key)
    out(f"smoke: {args.base_url}")
    out(render(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
