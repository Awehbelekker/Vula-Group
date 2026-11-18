"""18 Nov 2026: Railway had XERO_CLIENT_SECRET saved with a trailing space, so the setting read
empty. Vula reads the misspelt name when the right one isn't set, and the right one always wins."""
import config


def _fresh(monkeypatch, **env):
    monkeypatch.delenv("XERO_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("XERO_CLIENT_SECRET ", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k.replace("_SPACE", " "), v)
    return config.get_settings.__wrapped__().xero_client_secret   # a fresh read; the shared one is untouched


def test_the_misspelt_name_is_read_when_the_right_one_is_missing(monkeypatch):
    assert _fresh(monkeypatch, XERO_CLIENT_SECRET_SPACE="s3cret") == "s3cret"


def test_the_right_name_wins(monkeypatch):
    assert _fresh(monkeypatch, XERO_CLIENT_SECRET="right", XERO_CLIENT_SECRET_SPACE="wrong") == "right"


def test_neither_set_stays_empty(monkeypatch):
    assert _fresh(monkeypatch) == ""
