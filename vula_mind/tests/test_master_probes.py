"""Master Health's migration probes cover every boot sentinel, not just up to 108."""
from vula.api import master
from vula.startup_checks import _SENTINELS


def test_probes_include_every_sentinel():
    nums = {p[0] for p in master._all_probes()}
    assert {s[0] for s in _SENTINELS} <= nums
    assert "177" in nums and "069" in nums
