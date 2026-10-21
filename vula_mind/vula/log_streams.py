"""Which stream a log line goes to, so Railway labels it truthfully.

Railway tags everything on stderr as "error" and everything on stdout as "info", whatever the
line says. Uvicorn's own logger writes to stderr, so every deploy showed "Application startup
complete" as an error (20 Oct audit, go-live blocker 1); the app's own lines went to stdout, so
its real errors showed as info. Now both split the same way: below ERROR → stdout, ERROR and up
→ stderr. Used by vula/api/server.py (app logging) and log_config.json (uvicorn, via start.py).
"""
from __future__ import annotations

import logging
import sys


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
