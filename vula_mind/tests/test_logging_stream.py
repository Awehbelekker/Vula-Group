"""server.py's logging must never send routine lines to stderr. 2026-09-11: it had no stream= at
all, so Python's default (stderr) was used for EVERY log call — Railway tags a deploy's entire
stderr stream as "error" regardless of actual level, so an @level:error filter in the Railway
dashboard returned hundreds of routine INFO lines and zero real signal.

2026-10-20: the fix then was "everything to stdout", which made real errors show as info. Now
basicConfig uses vula/log_streams.handlers(): below ERROR to stdout, ERROR and up to stderr
(behaviour tested in tests/test_audit_quick_fixes_1020.py).

Source-inspection rather than actually re-running logging.basicConfig() in-process — the real
root logger is already configured by the time tests import anything, and reconfiguring it here
would be invasive/order-dependent for zero extra confidence."""
import inspect
import logging
import sys

import vula.api.server as server
from vula.log_streams import BelowError, handlers


def test_basicconfig_uses_the_split_handlers():
    source = inspect.getsource(server)
    call_start = source.index("logging.basicConfig(")
    call_src = source[call_start:call_start + 1600]
    assert "handlers=_log_handlers(" in call_src, (
        "logging.basicConfig() must use vula.log_streams.handlers — Python's default stream "
        "(stderr) makes Railway label every log line as 'error'")


def test_routine_lines_never_reach_stderr():
    out, err = handlers(logging.Formatter("%(message)s"))
    assert out.stream is sys.stdout and any(isinstance(f, BelowError) for f in out.filters)
    assert err.stream is sys.stderr and err.level == logging.ERROR
