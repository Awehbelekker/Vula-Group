"""Coach-app sign-in: enrolment codes, device + PIN, lockout, revocation, signed tokens."""
from datetime import timedelta

import pytest

from tests.tap_fakes import Clock, MemoryRepo
from vula.tap.appauth import AppAuth, AuthError, hash_pin, sign_token, verify_token

T = "tenant-a"
PHONE = "+27 80 000 0001"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    repo.members[T] = [
        {"id": "m1", "name": "Sipho Dlamini", "whatsapp": "27800000001", "role": "staff", "active": True},
        {"id": "m2", "name": "Boss", "whatsapp": "27800000002", "role": "owner", "active": True},
        {"id": "m3", "name": "No Phone", "whatsapp": "", "role": "staff", "active": True}]
    return AppAuth(repo, "secret", clock), repo, clock


def enrolled(auth, pin="2468"):
    code = auth.issue_enrol_code(T, "m1", "owner@x.co")["code"]
    return auth.enrol(T, PHONE, code, pin, "Sipho's phone")


def test_token_roundtrip_expiry_and_tamper():
    clock = Clock()
    tok = sign_token("s", {"t": "x", "exp": (clock() + timedelta(hours=1)).timestamp()})
    assert verify_token("s", tok, clock())["t"] == "x"
    assert verify_token("other", tok, clock()) is None
    assert verify_token("s", tok, clock() + timedelta(hours=2)) is None
    body, sig = tok.split(".")
    assert verify_token("s", body + "." + sig[:-2] + "AA", clock()) is None
    for junk in ("", "x", "a.b", "....", None):
        assert verify_token("s", junk or "", clock()) is None


def test_enrol_issues_device_and_working_token(env):
    auth, repo, _ = env
    r = enrolled(auth)
    assert r["member"] == {"id": "m1", "name": "Sipho Dlamini", "role": "staff"}
    a = auth.actor(r["access_token"])
    assert (a.tenant_id, a.member_id, a.sees_all) == (T, "m1", False)
    dev = next(iter(repo.devices.values()))
    assert dev["pin_hash"] != "2468" and "2468" not in str(dev) and dev["token_hash"] != r["device_token"]
    assert hash_pin("2468", dev["pin_salt"]) == dev["pin_hash"]


def test_manager_sees_all(env):
    auth, repo, _ = env
    code = auth.issue_enrol_code(T, "m2", "x")["code"]
    r = auth.enrol(T, "27800000002", code, "1357")
    assert auth.actor(r["access_token"]).sees_all is True


def test_code_is_single_use_and_new_code_voids_old(env):
    auth, *_ = env
    c1 = auth.issue_enrol_code(T, "m1", "o")["code"]
    c2 = auth.issue_enrol_code(T, "m1", "o")["code"]
    if c1 != c2:
        with pytest.raises(AuthError):
            auth.enrol(T, PHONE, c1, "2468")
    auth.enrol(T, PHONE, c2, "2468")
    with pytest.raises(AuthError):
        auth.enrol(T, PHONE, c2, "2468")                      # used


def test_wrong_code_five_times_burns_it(env):
    auth, *_ = env
    code = auth.issue_enrol_code(T, "m1", "o")["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(AuthError):
            auth.enrol(T, PHONE, wrong, "2468")
    with pytest.raises(AuthError):
        auth.enrol(T, PHONE, code, "2468")                    # right code, but burned


def test_code_expires_and_is_bound_to_the_members_number(env):
    auth, repo, clock = env
    code = auth.issue_enrol_code(T, "m1", "o")["code"]
    with pytest.raises(AuthError):
        auth.enrol(T, "27800000002", code, "2468")            # someone else's number
    with pytest.raises(AuthError):
        auth.enrol(T, "27899999999", code, "2468")            # not on the team
    clock.t += timedelta(minutes=11)
    with pytest.raises(AuthError):
        auth.enrol(T, PHONE, code, "2468")


def test_tenant_isolation_of_codes(env):
    auth, repo, _ = env
    repo.members["tenant-b"] = [{"id": "m1", "name": "X", "whatsapp": "27800000001", "role": "staff"}]
    code = auth.issue_enrol_code(T, "m1", "o")["code"]
    with pytest.raises(AuthError):
        auth.enrol("tenant-b", PHONE, code, "2468")


@pytest.mark.parametrize("pin", ["", "123", "1234567", "12ab", "abcd"])
def test_bad_pin_format_rejected(env, pin):
    auth, *_ = env
    code = auth.issue_enrol_code(T, "m1", "o")["code"]
    with pytest.raises(AuthError) as e:
        auth.enrol(T, PHONE, code, pin)
    assert e.value.status == 422


def test_cannot_issue_for_unknown_or_phoneless_member(env):
    auth, *_ = env
    with pytest.raises(AuthError):
        auth.issue_enrol_code(T, "nope", "o")
    with pytest.raises(AuthError, match="WhatsApp"):
        auth.issue_enrol_code(T, "m3", "o")


def test_login_with_pin_and_lockout(env):
    auth, repo, clock = env
    r = enrolled(auth)
    assert auth.login(r["device_token"], "2468")["access_token"]
    for i in range(4):
        with pytest.raises(AuthError) as e:
            auth.login(r["device_token"], "0000")
        assert e.value.status == 401
    with pytest.raises(AuthError) as e:
        auth.login(r["device_token"], "0000")                 # 5th wrong
    assert e.value.status == 423
    with pytest.raises(AuthError) as e:
        auth.login(r["device_token"], "2468")                 # right PIN, still locked
    assert e.value.status == 423
    clock.t += timedelta(minutes=16)
    assert auth.login(r["device_token"], "2468")["access_token"]


def test_right_pin_resets_the_failure_count(env):
    auth, *_ = env
    r = enrolled(auth)
    for _ in range(4):
        with pytest.raises(AuthError):
            auth.login(r["device_token"], "0000")
    auth.login(r["device_token"], "2468")
    for _ in range(4):
        with pytest.raises(AuthError) as e:
            auth.login(r["device_token"], "0000")
        assert e.value.status == 401                          # not locked: counter was reset


def test_unknown_device_token_rejected(env):
    auth, *_ = env
    with pytest.raises(AuthError):
        auth.login("not-a-token", "2468")
    with pytest.raises(AuthError):
        auth.login("", "")


def test_revoking_a_device_kills_existing_sessions_at_once(env):
    auth, repo, _ = env
    r = enrolled(auth)
    dev_id = next(iter(repo.devices))
    assert auth.actor(r["access_token"])
    auth.revoke_device(T, dev_id)
    with pytest.raises(AuthError):
        auth.actor(r["access_token"])
    with pytest.raises(AuthError):
        auth.login(r["device_token"], "2468")


def test_deactivated_member_loses_access(env):
    auth, repo, _ = env
    r = enrolled(auth)
    repo.members[T][0]["active"] = False
    with pytest.raises(AuthError) as e:
        auth.actor(r["access_token"])
    assert e.value.status == 403
    with pytest.raises(AuthError):
        auth.login(r["device_token"], "2468")


def test_access_token_expires(env):
    auth, _, clock = env
    r = enrolled(auth)
    clock.t += timedelta(hours=13)
    with pytest.raises(AuthError):
        auth.actor(r["access_token"])
