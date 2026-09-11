"""server.py's logging.basicConfig() must pin stream=sys.stdout. 2026-09-11: it had no
stream= at all, so Python's default (stderr) was used for EVERY log call — Railway tags a
deploy's entire stderr stream as "error" regardless of actual level, so an @level:error filter
in the Railway dashboard returned hundreds of routine INFO lines and zero real signal.

Source-inspection rather than actually re-running logging.basicConfig() in-process — the real
root logger is already configured by the time tests import anything, and reconfiguring it here
would be invasive/order-dependent for zero extra confidence; the thing that actually matters is
that the *call site* pins the stream, which a straight source read confirms unambiguously."""
import inspect

import vula.api.server as server


def test_basicconfig_pins_stdout():
    source = inspect.getsource(server)
    call_start = source.index("logging.basicConfig(")
    # Look at a generous window rather than hunting for the matching ")" — the format string
    # argument itself contains parens (e.g. "%(levelname)s"), so a naive first-")" search
    # would truncate before the actual call closes.
    call_src = source[call_start:call_start + 1200]
    assert "stream=sys.stdout" in call_src, (
        "logging.basicConfig() must pin stream=sys.stdout — omitting it silently falls back "
        "to stderr, which Railway mislabels as 'error' for every log line regardless of level")
