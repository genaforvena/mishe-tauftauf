"""Tests for the multi-body coverage reading.

The reading answers two different questions and the tests keep them apart:
which bodies can be observed right now, and whether a claim that crosses bodies
is supported at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from mishe_tauftauf.multibody import SENSE_ID, read_multibody_coverage

PRODUCER = {"name": "android-body-perception", "entry_point": "watch.py",
            "mode": "space", "contract": 1}
CHANNELS = ("battery", "light", "acceleration")


def _row(kind: str, sequence: int, now: float, *, age: float = 1.0,
         sample_age: float = 3.0) -> dict:
    return {"kind": kind, "sequence": sequence, "collected": now - sample_age,
            "received": now - age, "fields": {"lux": "10"},
            "backlog_page_pending_at_receipt": False}


def _consumer_state(node: str, now: float, *, age: float = 1.0,
                    sample_age: float = 3.0, poll_age: float = 1.0,
                    channels: tuple[str, ...] = CHANNELS) -> dict:
    latest = {kind: _row(kind, index + 1, now, age=age, sample_age=sample_age)
              for index, kind in enumerate(channels)}
    return {"version": 1, "node": node,
            "source": f"http://127.0.0.1:8765/node/{node}", "cursor": 11,
            "session": f"session-{node}", "latest": latest,
            "poll_at": now - poll_age, "has_more": False, "error": None}


def _clock_domain(now: float, *, offset: float = -0.9) -> dict:
    return {"version": 1, "status": "ok", "offset_nominal_s": offset,
            "offset_min_s": offset - 0.5, "offset_max_s": offset + 0.5,
            "observed_at_epoch_s": now, "validity_seconds": 3600.0,
            "samples": 3, "source": "tools/clock_offset.py", "reason": None,
            "jump": None}


def _site(tmp_path: Path, nodes: list[dict], now: float, *,
          delivery: dict[str, dict] | None = None,
          spool: dict[str, dict] | None = None,
          write_boundary: bool = True) -> Path:
    core = tmp_path / "core"
    android = tmp_path / "android-body"
    (core / "health").mkdir(parents=True)
    (core / "health" / "linked-sites.json").write_text(json.dumps({
        "version": 1,
        "sites": [{"home": str(android), "session": "android-body", "sha": "a" * 40}],
    }), encoding="utf-8")
    if write_boundary:
        (core / "body").mkdir(parents=True)
        (core / "body" / "space.json").write_text(json.dumps({
            "schema_version": 1, "producer": PRODUCER,
            "generated_at_utc": "2026-10-20T22:00:00+00:00", "nodes": nodes,
        }), encoding="utf-8")
    for name, state in (delivery or {}).items():
        path = android / "body" / "perception" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    for name, summary in (spool or {}).items():
        path = android / "body" / f"spool-summary-{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary), encoding="utf-8")
    return core


def _node(name: str, now: float, *, clock: bool = True) -> dict:
    node = {"node": name, "source": f"http://127.0.0.1:8765/node/{name}",
            "session": f"session-{name}", "cursor": 11, "last_poll_utc": "2026-10-20T22:00:00+00:00"}
    if clock:
        node["clock_domain"] = _clock_domain(now)
    return node


def _spool_summary(name: str, now: float, *, age: float = 2.0) -> dict:
    return {"node": name, "counts": {"light": 10}, "method": "adb-run-as-grep-counts",
            "newest_row_epoch_s": now - age, "oldest_row_epoch_s": now - 60,
            "observed_at_epoch_s": now, "scan_state": {"last_error": "none"}}


def test_missing_boundary_names_its_absence(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core = _site(tmp_path, [], now, write_boundary=False)

    row = read_multibody_coverage(core, now)

    assert row["id"] == SENSE_ID
    assert row["state"] == "unknown"
    assert row["reason"] == "boundary-missing"
    assert "body/space.json" in row["sample"]


def test_corrupt_boundary_is_unknown_not_empty(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core = _site(tmp_path, [], now)
    (core / "body" / "space.json").write_text("{broken", encoding="utf-8")

    row = read_multibody_coverage(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "boundary-absent-or-corrupt"


def test_boundary_from_another_producer_is_not_the_space_boundary(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core = _site(tmp_path, [], now)
    (core / "body" / "space.json").write_text(json.dumps({
        "schema_version": 1, "producer": {"name": "something-else"},
        "nodes": [_node("note3", now)],
    }), encoding="utf-8")

    row = read_multibody_coverage(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "boundary-wrong-producer"


def test_two_current_bodies_with_verified_clocks_support_a_joint_claim(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now),
                           "redmi10": _consumer_state("redmi10", now)})

    row = read_multibody_coverage(core, now)

    assert row["state"] == "verified"
    assert row["joint_state"] == "supported"
    assert row["joint_reason"] == "compatible-window-and-clock-verified"
    assert sorted(row["covered_nodes"]) == ["note3", "redmi10"]
    # The joint window is the overlap of the two bodies' own windows, so it
    # must be exactly that interval and not merely a well-formed pair.
    assert row["joint_window"] == {"start_epoch_s": now - 3, "end_epoch_s": now - 1}


def test_bodies_whose_windows_do_not_overlap_do_not_support_a_joint_claim(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now, age=18.0, sample_age=20.0),
                           "redmi10": _consumer_state("redmi10", now, age=1.0, sample_age=3.0)})

    row = read_multibody_coverage(core, now)

    assert row["joint_state"] == "unknown"
    assert row["joint_reason"] == "no-compatible-window"
    assert row["joint_window"] is None
    assert sorted(row["covered_nodes"]) == ["note3", "redmi10"]


def test_two_lanes_of_one_body_do_not_bridge_the_gap_between_them(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    # Body A has a current delivery window and an older spool row; body B has
    # only a spool row. The gap between A's two lanes is a time in which A was
    # not observed, so B's spool row must not be able to pair with it.
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now, age=1.0, sample_age=3.0)},
                 spool={"note3": _spool_summary("note3", now, age=899.0),
                        "redmi10": _spool_summary("redmi10", now, age=500.0)})

    row = read_multibody_coverage(core, now)

    assert sorted(row["covered_nodes"]) == ["note3", "redmi10"]
    assert row["joint_state"] == "unknown"
    assert row["joint_reason"] == "no-compatible-window"
    assert row["joint_window"] is None


def test_a_future_spool_row_is_not_evidence(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now)]
    core = _site(tmp_path, nodes, now,
                 spool={"note3": _spool_summary("note3", now, age=-3600.0)})

    row = read_multibody_coverage(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "coverage-unreadable"
    assert row["unknown_nodes"] == ["note3"]


def test_two_future_spool_rows_do_not_support_a_joint_claim(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 spool={"note3": _spool_summary("note3", now, age=-3600.0),
                        "redmi10": _spool_summary("redmi10", now, age=-3600.0)})

    row = read_multibody_coverage(core, now)

    assert row["joint_state"] != "supported"
    assert row["state"] == "unknown"
    assert row["reason"] == "coverage-unreadable"
    assert sorted(row["unknown_nodes"]) == ["note3", "redmi10"]


def test_a_future_phone_sample_is_not_fresh_evidence(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now)]
    state = _consumer_state("note3", now)
    state["latest"]["light"]["collected"] = now + 3600
    core = _site(tmp_path, nodes, now, delivery={"note3": state})

    row = read_multibody_coverage(core, now)

    note3 = row["nodes"][0]
    assert note3["delivery"]["future_channels"] == ["light"]
    assert note3["delivery"]["fresh_channels"] == ["acceleration", "battery"]
    assert note3["delivery"]["oldest_fresh_epoch_s"] == now - 1
    assert note3["coverage"] == "covered"


def test_a_stale_spool_row_leaves_the_body_uncovered(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now)]
    core = _site(tmp_path, nodes, now,
                 spool={"note3": _spool_summary("note3", now, age=7200.0)})

    row = read_multibody_coverage(core, now)

    assert row["covered_nodes"] == []
    assert row["stale_nodes"] == ["note3"]


def test_a_missing_linked_site_leaves_every_body_unknown(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now)]
    core = _site(tmp_path, nodes, now)
    (core / "health" / "linked-sites.json").unlink()

    row = read_multibody_coverage(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "coverage-unreadable"
    assert row["unknown_nodes"] == ["note3"]
    assert "linked-site-registry-missing" in row["nodes"][0]["delivery"]["reason"]


def test_a_malformed_consumer_state_leaves_that_body_unknown(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now),
                           "redmi10": {"version": 1, "node": "redmi10"}})

    row = read_multibody_coverage(core, now)

    assert row["covered_nodes"] == ["note3"]
    assert row["unknown_nodes"] == ["redmi10"]
    assert row["nodes"][1]["delivery"]["reason"] == "consumer-state-invalid"


def test_a_stale_body_leaves_the_joint_claim_unsupported(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now, age=600.0, sample_age=610.0),
                           "redmi10": _consumer_state("redmi10", now)})

    row = read_multibody_coverage(core, now)

    assert row["joint_state"] == "unknown"
    assert row["joint_reason"] == "no-multi-body-window"
    assert row["covered_nodes"] == ["redmi10"]
    assert row["stale_nodes"] == ["note3"]
    assert "note3 coverage=stale" in row["sample"]


def test_a_body_without_a_verified_clock_domain_blocks_the_joint_claim(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now, clock=False)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now),
                           "redmi10": _consumer_state("redmi10", now)})

    row = read_multibody_coverage(core, now)

    assert row["joint_state"] == "unknown"
    assert row["joint_reason"] == "clock-domain-unverified:redmi10"
    assert "clock-domain-unverified:redmi10" in row["joint_blockers"]


def test_a_body_read_by_the_spool_lane_is_covered_while_its_delivery_is_frozen(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now), _node("redmi10", now)]
    core = _site(tmp_path, nodes, now,
                 delivery={"note3": _consumer_state("note3", now, age=600.0, sample_age=610.0),
                           "redmi10": _consumer_state("redmi10", now)},
                 spool={"note3": _spool_summary("note3", now)})

    row = read_multibody_coverage(core, now)

    assert sorted(row["covered_nodes"]) == ["note3", "redmi10"]
    assert row["joint_state"] == "supported"
    note3 = next(item for item in row["nodes"] if item["node"] == "note3")
    assert note3["delivery"]["fresh_channels"] == []
    assert note3["spool"]["current"] is True


def test_a_body_with_no_lane_at_all_is_unknown_not_current(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    nodes = [_node("note3", now)]
    core = _site(tmp_path, nodes, now)

    row = read_multibody_coverage(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "coverage-unreadable"
    assert row["unknown_nodes"] == ["note3"]
