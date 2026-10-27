"""Which stream a log line goes to, so Railway labels it truthfully.

Railway tags everything on stderr as "error" and everything on stdout as "info", whatever the
line says. Uvicorn's own logger writes to stderr, so every deploy showed "Application startup
complete" as an error (20 Oct audit, go-live blocker 1); the app's own lines went to stdout, so
its real errors showed as info. Now both split the same way: below ERROR → stdout, ERROR and up
→ stderr. Used by vula/api/server.py (app logging) and log_config.json (uvicorn, via start.py).

2026-10-27 (gap audit 1.7): the stream split still left WARNING lines labelled "info", and no
line said which tenant or request it belonged to. JsonFormatter writes each line as one JSON
object, which Railway parses: `level` sets the label (warn shows as warn), `message` is the
text, and `tenant` / `req` become filters (@tenant:digg-demo, @req:3f9c2a1b). Plain text stays
for local development (DEBUG=true or LOG_FORMAT=text).
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

# Set per HTTP request by vula/api/server.py's request middleware. Copied into any task the
# request starts, so a WhatsApp turn's background work keeps its labels.
_req: contextvars.ContextVar = contextvars.ContextVar("vula_log_req", default=None)
_tenant: contextvars.ContextVar = contextvars.ContextVar("vula_log_tenant", default=None)

_LEVELS = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warn"}


def bind(*, req: Optional[str] = None, tenant: Optional[str] = None) -> None:
    """Label every log line from here on (in this request / task) with req and tenant."""
    if req is not None:
        _req.set(req)
    if tenant is not None:
        _tenant.set(tenant)


def _current_tenant() -> Optional[str]:
    # The metering contextvar is set deeper in (WhatsApp resolves its tenant from the phone
    # number, not the path), so prefer it over the path-derived label.
    try:
        from vula.integrations.metering import get_request_tenant
        t = get_request_tenant()
        if t:
            return t
    except Exception:
        pass
    return _tenant.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in the shape Railway's log explorer reads."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        elif record.exc_text:
            msg = msg + "\n" + record.exc_text
        out = {
            "level": _LEVELS.get(record.levelno, "error" if record.levelno >= logging.ERROR else "info"),
            "message": msg,
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
        }
        tenant, req = _current_tenant(), _req.get()
        if tenant:
            out["tenant"] = tenant
        if req:
            out["req"] = req
        return json.dumps(out, ensure_ascii=False, default=str)



class BelowError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def handlers(fmt: logging.Formatter) -> list:
    out = logging.StreamHandler(sys.stdout)
    out.addFilter(BelowError())
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    for h in (out, err):
        h.setFormatter(fmt)
    return [out, err]
