from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf import unit_cpu


def _write_cpu(path: Path, usage: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cpu.stat").write_text(f"usage_usec {usage}\nuser_usec 0\nsystem_usec 0\n", encoding="utf-8")


def test_verdict_attributes_units_and_reconciles_within_tolerance() -> None:
    before = (1_000_000, {"a.service": 100_000, "b.scope": 200_000})
    after = (2_000_000, {"a.service": 600_000, "b.scope": 380_000})
    # Root delta 1.0 s over a 1.0 s window is 100% of one core; units sum to 0.68 s, a 32% gap.
    reading = unit_cpu.unit_cpu_verdict(before, after, 1.0)
    assert reading["state"] == "drift"
    assert reading["unit_sum_usec"] == 680_000
    assert reading["root_usec"] == 1_000_000

    # Unit deltas 500 ms + 490 ms = 990 ms against a 1000 ms root delta: a 1% gap, within tolerance.
    ok_after = (2_000_000, {"a.service": 600_000, "b.scope": 690_000})
    reading = unit_cpu.unit_cpu_verdict(before, ok_after, 1.0)
    assert reading["state"] == "verified"
    assert round(reading["reconcile_gap"], 3) == 0.01
    assert reading["units"][0] == {"unit": "a.service", "usec": 500_000, "rate": 50.0}
    assert reading["sample"].startswith("mishe-plants.slice root=100.0% of one core units=99.0%")


def test_unit_born_in_window_is_attributed_in_full_and_exited_unit_is_named() -> None:
    before = (0, {"gone.scope": 50_000, "steady.service": 0})
    # The slice counter also holds the 50 ms the exited scope used, so it reads 550 ms.
    after = (550_000, {"steady.service": 400_000, "new.scope": 100_000})
    reading = unit_cpu.unit_cpu_verdict(before, after, 0.55)
    assert {row["unit"]: row["usec"] for row in reading["units"]} == {"steady.service": 400_000,
                                                                       "new.scope": 100_000}
    assert reading["exited_in_window"] == ["gone.scope"]
    # 50 ms of 550 ms is unattributed (9.1%), beyond tolerance: drift, not a quiet attribution.
    assert reading["state"] == "drift"
    assert round(reading["reconcile_gap"], 3) == 0.091


def test_recycled_name_with_a_lower_counter_is_not_counted() -> None:
    before = (0, {"x.scope": 900_000})
    after = (100_000, {"x.scope": 10_000})
    reading = unit_cpu.unit_cpu_verdict(before, after, 0.1)
    assert reading["unit_sum_usec"] == 0
    assert reading["units"] == []


def test_zero_root_delta_with_zero_units_is_idle_and_verified() -> None:
    reading = unit_cpu.unit_cpu_verdict((5, {"a.service": 1}), (5, {"a.service": 1}), 1.0)
    assert reading["state"] == "verified"
    assert reading["reconcile_gap"] == 0.0


def test_backwards_root_counter_and_bad_window_are_unknown() -> None:
    assert unit_cpu.unit_cpu_verdict((10, {}), (5, {}), 1.0)["state"] == "unknown"
    assert unit_cpu.unit_cpu_verdict((0, {}), (0, {}), 0.0)["state"] == "unknown"


def test_read_unit_cpu_samples_a_fake_slice_across_the_window(tmp_path: Path) -> None:
    slice_path = tmp_path / unit_cpu.SLICE
    _write_cpu(slice_path, 1_000)
    _write_cpu(slice_path / "tmux-spawn-a.scope", 100)
    _write_cpu(slice_path / "mishe-senses.service", 200)
    (slice_path / "not-a-unit").mkdir()

    def advance(_seconds: float) -> None:
        _write_cpu(slice_path, 1_000 + 300_000)
        _write_cpu(slice_path / "tmux-spawn-a.scope", 100 + 200_000)
        _write_cpu(slice_path / "mishe-senses.service", 200 + 100_000)

    with patch("mishe_tauftauf.unit_cpu.time.sleep", side_effect=advance):
        reading = unit_cpu.read_unit_cpu(cgroup_root=tmp_path, sample_seconds=0.3)
    assert reading["id"] == unit_cpu.SENSE_ID
    assert reading["root_usec"] == 300_000
    assert reading["unit_sum_usec"] == 300_000
    assert reading["state"] == "verified"


def test_read_unit_cpu_is_unknown_when_the_slice_is_unreadable(tmp_path: Path) -> None:
    with patch("mishe_tauftauf.unit_cpu.time.sleep"):
        reading = unit_cpu.read_unit_cpu(cgroup_root=tmp_path)
    assert reading["state"] == "unknown"
    assert "not measured, not calm" in reading["sample"]
