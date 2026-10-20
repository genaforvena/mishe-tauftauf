"""Multi-body coverage: which bodies this plant can observe, and what they support.

The plant carries more than one body. ``sense.space.light`` deliberately reads
one of them (the Note3), so a reader of the core summary can see a body while
never learning that another body is live. This sense reads every body the
shared space boundary names and reports two separate things:

- the *coverage* of each body: whether some lane carries evidence about it
  right now, how old that evidence is, and which lane it came from;
- the *joint* verdict: whether a claim that crosses bodies is supported at
  all, and, when it is not, which condition blocks it.

The two are kept apart on purpose. A body whose HTTP delivery channel is
frozen by design is still a body that is being read, and a reader who saw only
one channel would call a live body absent. The joint verdict is the consumer's
answer: a fused reading needs every body current inside one compatible
observation window, and it needs each body's clock domain verified, because a
composite timestamp cannot refresh a stale input and two bodies' clocks are
not one clock.
"""

from __future__ import annotations

import time
from pathlib import Path

from .body_perception import (FRESH_SECONDS, read_node_consumer_state,
                              read_node_spool_summary)
from .space_text import (SPACE_PRODUCER, decode_clock_domain,
                         decode_space_boundary)

SENSE_ID = "sense.body.multibody.coverage"
#: The same identity under the name the pane's row set uses.
MULTIBODY_SENSE_ID = SENSE_ID
#: How long one lane's evidence stays current for the coverage question. It
#: matches the consumer's own freshness gate, so a lane the consumer calls
#: fresh is a lane this reading calls current.
FRESHNESS_SECONDS = FRESH_SECONDS
#: The spool lane publishes on the linked site's own reader cadence, measured
#: at roughly 150 s, and carries rows the phone produced at about 5 s
#: cadence. A 15-minute bound sits an order of magnitude above that
#: publication lag and far below the timescale of a body that has stopped
#: delivering, so a lane whose rows stop advancing crosses it and reads stale
#: while a live lane stays covered.
SPOOL_ROW_GATE_SECONDS = 900.0


def _unknown(reason: str, sample: str, details: str = "") -> dict[str, object]:
    row: dict[str, object] = {"id": SENSE_ID, "kind": "read", "state": "unknown",
                               "reason": reason, "sample": sample,
                               "joint_state": "unknown", "joint_reason": reason}
    if details:
        row["details"] = details
    return row


def _space_boundary(home: Path) -> tuple[dict | None, str]:
    """The decoded shared space boundary, or None with the reason it failed."""
    path = home / "body" / "space.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "boundary-missing"
    except UnicodeDecodeError:
        return None, "boundary-malformed"
    except OSError:
        return None, "boundary-unreadable"
    decoded = decode_space_boundary(text)
    if decoded is None:
        return None, "boundary-absent-or-corrupt"
    if (type(decoded.get("schema_version")) is not int
            or decoded.get("schema_version") != 1
            or decoded.get("producer") != SPACE_PRODUCER):
        return None, "boundary-wrong-producer"
    return decoded, ""


def _clock_state(node: dict, now: float) -> dict[str, object]:
    """The body's own clock-domain verdict, read without renewing its age."""
    reading = decode_clock_domain(node.get("clock_domain"))
    if reading is None:
        return {"state": "absent", "reason": "no-clock-domain-file",
                "offset_s": None, "age_seconds": None}
    observed = reading.get("observed_at_epoch_s")
    validity = reading.get("validity_seconds")
    nominal = reading.get("offset_nominal_s")
    lower = reading.get("offset_min_s")
    upper = reading.get("offset_max_s")
    if reading.get("status") != "ok":
        return {"state": "unknown", "reason": reading.get("reason") or "clock-domain-status",
                "offset_s": nominal, "age_seconds": None}
    if any(value is None for value in (observed, validity, nominal, lower, upper)):
        return {"state": "unknown", "reason": "incomplete-clock-domain",
                "offset_s": nominal, "age_seconds": None}
    if validity <= 0 or lower > upper or not lower <= nominal <= upper:
        return {"state": "unknown", "reason": "inconsistent-clock-domain",
                "offset_s": nominal, "age_seconds": None}
    if observed > now:
        return {"state": "unknown", "reason": "clock-domain-future",
                "offset_s": nominal, "age_seconds": None}
    age = now - observed
    if age > validity:
        return {"state": "unknown", "reason": "clock-domain-expired",
                "offset_s": nominal, "age_seconds": age}
    return {"state": "verified", "reason": "clock-domain-current",
            "offset_s": nominal, "age_seconds": age}


def _delivery_lane(lane: dict[str, object], now: float) -> dict[str, object]:
    """The delivery lane's coverage, with its fresh evidence window."""
    channels = lane.get("channels") if isinstance(lane.get("channels"), dict) else {}
    fresh: list[float] = []
    samples: list[float] = []
    fresh_names: list[str] = []
    stale: list[str] = []
    future: list[str] = []
    intervals: list[dict[str, object]] = []
    for kind, entry in channels.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "fresh-clock-conditional":
            stale.append(f"{kind}={entry.get('status')}")
            continue
        receipt = entry.get("consumer_receipt_epoch_s")
        sample = entry.get("phone_sample_epoch_s")
        if not isinstance(receipt, (int, float)) or not isinstance(sample, (int, float)):
            stale.append(f"{kind}=unknown-time")
            continue
        if float(receipt) > now + 5 or float(sample) > now + 5:
            # A row dated in the future is not evidence about now: its age
            # would be negative and would sit inside every freshness gate.
            future.append(kind)
            continue
        fresh.append(float(receipt))
        samples.append(float(sample))
        fresh_names.append(kind)
        # One channel's own observation interval: from the phone sample it
        # carries to the host receipt that delivered it.
        intervals.append({"lane": f"delivery:{kind}", "start_epoch_s": float(sample),
                          "end_epoch_s": float(receipt)})
    summary: dict[str, object] = {
        "available": bool(lane.get("available")),
        "reason": lane.get("reason"),
        "fresh_channels": sorted(fresh_names),
        "stale_channels": stale,
        "future_channels": sorted(future),
        "intervals": intervals,
        "oldest_fresh_epoch_s": min(fresh) if fresh else None,
        "newest_fresh_epoch_s": max(fresh) if fresh else None,
        "oldest_sample_epoch_s": min(samples) if samples else None,
        "poll_age_seconds": lane.get("poll_age_seconds"),
        "cursor": lane.get("cursor"),
        "session": lane.get("session"),
    }
    return summary


def _spool_lane(lane: dict[str, object], now: float) -> dict[str, object]:
    """The local spool lane's coverage, with its newest row's age."""
    newest = lane.get("newest_row_epoch_s")
    age = lane.get("age_seconds")
    if age is None and isinstance(newest, (int, float)):
        age = now - float(newest)
    future = age is not None and age < -5
    return {
        "available": bool(lane.get("available")),
        "reason": lane.get("reason"),
        "newest_row_epoch_s": newest,
        "age_seconds": age,
        "future": future,
        "current": bool(lane.get("available")) and age is not None
        and not future and age <= SPOOL_ROW_GATE_SECONDS,
        "counts": lane.get("counts"),
    }


def _coverage(node: dict, delivery: dict[str, object],
              spool: dict[str, object], now: float) -> dict[str, object]:
    """One body's coverage class and the intervals its evidence covers.

    A body is evidenced over a *set* of intervals, one per channel or lane,
    not over a single span: the gap between a current delivery window and an
    older spool row is a time in which the body was not observed, and
    bridging it would let a cross-body claim be placed where no observation
    exists.
    """
    delivery_summary = _delivery_lane(delivery, now)
    spool_summary = _spool_lane(spool, now)
    intervals: list[dict[str, object]] = []
    for interval in delivery_summary["intervals"]:
        intervals.append(interval)
    if spool_summary["current"] and isinstance(spool_summary["newest_row_epoch_s"], (int, float)):
        intervals.append({"lane": "spool", "start_epoch_s": float(spool_summary["newest_row_epoch_s"]),
                          "end_epoch_s": float(spool_summary["newest_row_epoch_s"])})
    if intervals:
        coverage = "covered"
        reason = "lane-evidence-alive"
    elif delivery_summary["available"] or spool_summary["available"]:
        coverage = "stale"
        reason = "lane-evidence-stale"
    else:
        coverage = "unknown"
        reason = "no-lane-evidence"
    return {
        "node": node.get("node"),
        "source": node.get("source"),
        "coverage": coverage,
        "reason": reason,
        "intervals": intervals,
        "delivery": delivery_summary,
        "spool": spool_summary,
        "clock": _clock_state(node, now),
    }


def _joint(coverage: list[dict[str, object]]) -> dict[str, object]:
    """The cross-body verdict: supported, or the condition that blocks it.

    Two bodies can be compared over a time only where both of them have
    evidence. Each body's evidence is a set of intervals, so the shared times
    are the pairwise overlaps of those intervals: an overlap is a time both
    bodies were observed, and a claim placed there is supported. No overlap
    means the bodies were never observed at the same time, and the reading
    says so instead of bridging the gap between two lanes.
    """
    covered = [item for item in coverage if item.get("coverage") == "covered"]
    names = [str(item.get("node")) for item in covered]
    blockers: list[str] = []
    overlaps: list[dict[str, object]] = []
    if len(covered) < 2:
        blockers.append("no-multi-body-window")
    else:
        for index, first in enumerate(covered):
            for second in covered[index + 1:]:
                left = first.get("intervals") if isinstance(first.get("intervals"), list) else []
                right = second.get("intervals") if isinstance(second.get("intervals"), list) else []
                for one in left:
                    for other in right:
                        if not isinstance(one, dict) or not isinstance(other, dict):
                            continue
                        start = max(float(one["start_epoch_s"]), float(other["start_epoch_s"]))
                        end = min(float(one["end_epoch_s"]), float(other["end_epoch_s"]))
                        if start <= end:
                            overlaps.append({"nodes": [str(first.get("node")), str(second.get("node"))],
                                             "start_epoch_s": start, "end_epoch_s": end})
        if not overlaps:
            blockers.append("no-compatible-window")
    unverified = [str(item.get("node")) for item in coverage
                  if isinstance(item.get("clock"), dict)
                  and item["clock"].get("state") != "verified"]
    if unverified:
        blockers.append("clock-domain-unverified:" + ",".join(sorted(unverified)))
    window = None
    if overlaps:
        window = {"start_epoch_s": min(item["start_epoch_s"] for item in overlaps),
                  "end_epoch_s": max(item["end_epoch_s"] for item in overlaps)}
    if blockers:
        return {"state": "unknown", "reason": blockers[0], "blockers": blockers,
                "covered_nodes": names, "window": None, "overlaps": []}
    return {"state": "supported", "reason": "compatible-window-and-clock-verified",
            "blockers": [], "covered_nodes": names, "window": window,
            "overlaps": overlaps}


def _age_text(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    seconds = float(value)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def read_multibody_coverage(home: Path, now: float | None = None) -> dict[str, object]:
    """Report every body's coverage and whether a cross-body claim is supported.

    The reading is honest about the difference between "this body is being
    read" and "these bodies can be compared". Each body is classified by the
    evidence that exists for it right now, and the joint verdict names the
    first condition that blocks a fused reading, with the rest of the blockers
    kept visible. A body whose delivery channel is frozen while another lane
    still reads it is reported as covered, because the coverage question is
    about the body, not about one channel.
    """
    current = time.time() if now is None else float(now)
    data, error = _space_boundary(home)
    if data is None:
        return _unknown(error,
                        f"multibody coverage UNKNOWN: {error}; "
                        "source=this plant's body/space.json",
                        "the shared space boundary names the bodies; without it "
                        "the body inventory is unknown, not empty.")
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return _unknown("boundary-nodes-absent",
                        "multibody coverage UNKNOWN: boundary-nodes-absent; "
                        "source=this plant's body/space.json")
    coverage: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("node")
        if not isinstance(name, str):
            continue
        delivery = read_node_consumer_state(home, name, current)
        spool = read_node_spool_summary(home, name, current)
        coverage.append(_coverage(node, delivery, spool, current))
    if not coverage:
        return _unknown("boundary-nodes-unusable",
                        "multibody coverage UNKNOWN: boundary-nodes-unusable; "
                        "source=this plant's body/space.json")
    joint = _joint(coverage)
    covered_names = [str(item["node"]) for item in coverage
                     if item.get("coverage") == "covered"]
    stale_names = [str(item["node"]) for item in coverage
                   if item.get("coverage") == "stale"]
    unknown_names = [str(item["node"]) for item in coverage
                     if item.get("coverage") == "unknown"]
    if not covered_names and not stale_names:
        row = _unknown("coverage-unreadable",
                       "multibody coverage UNKNOWN: coverage-unreadable; "
                       "no lane carried evidence for any body",
                       "every body's lanes were unreadable, so no body's "
                       "coverage is known.")
        row["unknown_nodes"] = unknown_names
        row["nodes"] = coverage
        return row
    parts = []
    for item in coverage:
        delivery = item["delivery"]
        spool = item["spool"]
        clock = item["clock"]
        parts.append(
            f"{item['node']} coverage={item['coverage']}"
            f"/{item['reason']}"
            f" delivery={'current' if delivery['oldest_fresh_epoch_s'] is not None else 'not-current'}"
            f"({_age_text(delivery['poll_age_seconds'])} poll)"
            f" spool={'alive' if spool['current'] else spool['reason']}"
            f"({_age_text(spool['age_seconds'])} row)"
            f" clock={clock['state']}"
        )
    sample = (f"joint={joint['state'].upper()}({joint['reason']}) "
              f"bodies={len(coverage)} covered={len(covered_names)}"
              f"({','.join(covered_names) if covered_names else 'none'}) "
              f"stale={len(stale_names)} unknown={len(unknown_names)}; "
              + "; ".join(parts))
    details = ("source=this plant's body/space.json (body inventory, clock "
               "domains) + linked android-body body/perception/<node>.json "
               "(delivery lane) + linked android-body "
               "body/spool-summary-<node>.json (local spool lane, published "
               "only for the bodies the site reads that way); "
               f"delivery freshness gate={FRESHNESS_SECONDS:g}s; "
               f"spool row gate={SPOOL_ROW_GATE_SECONDS:g}s; "
               "a body is covered when one lane carries evidence inside its "
               "gate, so a body whose HTTP delivery is frozen by design but "
               "whose spool lane still reads is covered, not absent; "
               "covered means the lane is alive, not that its sample is "
               "seconds old: the ages are printed beside each lane; "
               "the joint verdict needs at least two covered bodies whose "
               "observation windows overlap and a verified clock domain per "
               "body; a composite timestamp cannot refresh a stale input; "
               "clock-domain-unverified lists the bodies whose clock offset is "
               "not verified, so absolute-time cross-body claims stay UNKNOWN "
               "for them; the coverage class describes the body, not a "
               "person's movement, and acceleration includes gravity.")
    return {"id": SENSE_ID, "kind": "read", "state": "verified",
            "reason": "coverage-read-complete",
            "sample": sample, "details": details,
            "joint_state": joint["state"], "joint_reason": joint["reason"],
            "joint_blockers": joint["blockers"],
            "joint_window": joint["window"],
            "covered_nodes": covered_names, "stale_nodes": stale_names,
            "unknown_nodes": unknown_names, "nodes": coverage}
