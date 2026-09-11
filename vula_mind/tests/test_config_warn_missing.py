"""config.Settings.warn_missing(): genuine config gaps still warn (and are returned for the
caller to act on); PayFast/Resend being unset is a confirmed go-live decision (2026-09-11,
Yoco covers payments) and must log as "disabled by design" at INFO, not as a WARNING that
reads like a forgotten setting in an incident review — and must NOT appear in the returned
warnings list, since there's nothing here for anyone to go fix."""
import logging

from config import Settings


def _settings(**overrides):
    # Settings is a pydantic-settings model — construct directly rather than mutating the
    # process-wide `config.settings` singleton, so this can't leak into other tests.
    base = dict(api_key="k", supabase_url="https://real.supabase.co",
               supabase_service_key="real-key", whatsapp_token="real-permanent-token",
               payfast_merchant_id="", resend_api_key="")
    base.update(overrides)
    return Settings(**base)


def test_real_gaps_are_returned_and_warned(caplog):
    s = _settings(api_key="")
    with caplog.at_level(logging.WARNING, logger="vula.config"):
        warnings = s.warn_missing()
    assert any("API_KEY" in w for w in warnings)
    assert any("API_KEY" in r.message for r in caplog.records if r.levelno == logging.WARNING)


def test_payfast_and_resend_disabled_by_design_not_in_warnings(caplog):
    s = _settings()   # payfast_merchant_id="", resend_api_key="" — the actual prod state
    with caplog.at_level(logging.INFO, logger="vula.config"):
        warnings = s.warn_missing()
    assert not any("PAYFAST" in w or "RESEND" in w for w in warnings)
    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("disabled by design" in m and "PayFast" in m for m in info_msgs)
    assert any("disabled by design" in m and "Resend" in m for m in info_msgs)
    # Never a WARNING for these two, specifically.
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("PayFast" in m or "Resend" in m for m in warn_msgs)


def test_configured_payfast_and_resend_produce_no_log_line(caplog):
    s = _settings(payfast_merchant_id="real-merchant-id", resend_api_key="re_real_key")
    with caplog.at_level(logging.INFO, logger="vula.config"):
        s.warn_missing()
    assert not any("PayFast" in r.message or "Resend" in r.message for r in caplog.records)
