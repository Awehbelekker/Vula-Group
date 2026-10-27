"""Gap audit 1.7: log lines Railway can label and filter.

Railway parses a one-line JSON object: `level` sets the label, `message` is the text, and every
other field is a filter (@tenant:…, @req:…). Before this, WARNING lines showed as "info" and no
line said which tenant or request it came from."""
import asyncio
import json
import logging

from vula import log_streams
from vula.log_streams import JsonFormatter, bind


def _line(level, msg, *args, exc_info=None):
    rec = logging.LogRecord("vula.test", level, __file__, 1, msg, args, exc_info)
    return json.loads(JsonFormatter().format(rec))


def test_levels_are_the_ones_railway_reads():
    assert _line(logging.DEBUG, "d")["level"] == "debug"
    assert _line(logging.INFO, "i")["level"] == "info"
    assert _line(logging.WARNING, "w")["level"] == "warn"
    assert _line(logging.ERROR, "e")["level"] == "error"
    assert _line(logging.CRITICAL, "c")["level"] == "error"


def test_message_is_formatted_and_one_line():
    rec = logging.LogRecord("vula.test", logging.INFO, __file__, 1, "booked %s for R%d", ("bill", 908), None)
    raw = JsonFormatter().format(rec)
    assert "\n" not in raw
    out = json.loads(raw)
    assert out["message"] == "booked bill for R908" and out["logger"] == "vula.test" and out["time"]


def test_traceback_stays_on_the_same_line():
    try:
        raise ValueError("bad total")
    except ValueError:
        import sys
        raw = JsonFormatter().format(logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()))
    assert "\n" not in raw
    assert "ValueError: bad total" in json.loads(raw)["message"]


def test_request_and_tenant_labels():
    async def one_request():
        bind(req="ab12cd34", tenant="digg-demo")
        return _line(logging.INFO, "x")

    out = asyncio.run(one_request())
    assert out["req"] == "ab12cd34" and out["tenant"] == "digg-demo"
    # labels belong to that request only
    assert "req" not in _line(logging.INFO, "y") and "tenant" not in _line(logging.INFO, "y")


def test_whatsapp_tenant_wins_over_the_path(monkeypatch):
    # WhatsApp finds its tenant from the phone number, deeper than the path, via metering.
    from vula.integrations import metering

    async def turn():
        bind(req="r1")
        metering.set_request_tenant("oth")
        return _line(logging.WARNING, "x")

    out = asyncio.run(turn())
    assert out["tenant"] == "oth" and out["level"] == "warn"


def test_server_picks_the_tenant_from_the_path():
    from vula.api.server import _path_tenant
    assert _path_tenant("/v1/commerce/digg-demo/admin/invoices") == "digg-demo"
    assert _path_tenant("/v1/projects/gerflor/x") == "gerflor"
    assert _path_tenant("/status") is None


def test_server_logs_json_unless_text_is_asked_for():
    import inspect
    import vula.api.server as server
    src = inspect.getsource(server)
    call = src[src.index("logging.basicConfig("):][:1600]
    assert "_JsonFormatter()" in call and 'log_format != "text"' in call


def test_uvicorn_lines_are_json_too():
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parent.parent / "log_config.json").read_text())
    assert {h["formatter"] for h in cfg["handlers"].values()} == {"json"}
    assert cfg["formatters"]["json"]["()"] == "vula.log_streams.JsonFormatter"
    assert log_streams.JsonFormatter
