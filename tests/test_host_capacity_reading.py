from mishe_tauftauf import host_capacity_reading as hcr

GB = 1024 ** 3


def _sample(**over):
    base = {"anon": 2 * GB, "file": 9 * GB, "current": 11 * GB, "high": 16 * GB, "max": 17 * GB,
            "memory_full": 0.0, "io_full": 1.0, "available_ratio": 0.6,
            "events": {"oom_kill": 0}}
    base.update(over)
    return base


def test_file_cache_alone_does_not_limit_the_signal_rule():
    # current is 0.69*high (cache-heavy), anon is 0.13*high: the signal rule stays NORMAL.
    line = hcr.dashboard_line(_sample(), previous=None, at=100.0, now=100.0)
    assert "signal rule=NORMAL" in line
    assert "current/high 0.69" in line and "anon/high 0.12" in line


def test_real_anon_growth_still_limits_the_signal_rule():
    s = _sample(anon=15.8 * GB, current=16.5 * GB)
    line = hcr.dashboard_line(s, previous=None, at=100.0, now=100.0)
    assert "signal rule=CRITICAL" in line  # anon 0.99*high >= 0.9*max


def test_missing_part_is_unknown_not_normal():
    s = _sample()
    del s["file"]
    line = hcr.dashboard_line(s, previous=None, at=100.0, now=100.0)
    assert line.startswith("UNKNOWN") and "file" in line


def test_stale_sample_is_unknown_not_normal():
    line = hcr.dashboard_line(_sample(), previous=None, at=100.0, now=1000.0, max_age=60.0)
    assert line.startswith("UNKNOWN")


def test_anon_alone_at_max_reaches_critical():
    # Genome blocker (2026-11-01): anon-based decide must still reach CRITICAL when anon alone is at memory.max.
    s = _sample(anon=17 * GB, current=17 * GB)
    line = hcr.dashboard_line(s, previous=None, at=100.0, now=100.0)
    assert "signal rule=CRITICAL" in line


def test_anon_at_ninety_percent_of_max_reaches_critical_boundary():
    s = _sample(anon=17 * GB * .9, current=17 * GB * .9)  # exact float boundary of max*.9
    obs = hcr.controller_observation(s, at=100.0, now=100.0)
    assert hcr.host_capacity.decide(None, obs, now=100.0)["state"] == "CRITICAL"


def test_anon_just_below_ninety_percent_of_max_is_not_critical_on_max_rule():
    s = _sample(anon=17 * GB * .89, current=17 * GB * .89)
    obs = hcr.controller_observation(s, at=100.0, now=100.0)
    assert hcr.host_capacity.decide(None, obs, now=100.0)["state"] != "CRITICAL"
