"""Stable text panes for discovery, sensing, health, and operator decisions."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import re
import sys
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path

from .access import list_requests, recoveries, retired_requests
from .ci_watch import line as ci_line
from . import clear_journal
from . import scan_freshness
from .discovery import (latest, producer_label, scan_attempt, _tailnet_peer_read,
                        _space_clock_domains_read, _space_light, _space_light_name_freeze,
                        _space_light_read)
from .body_perception import (BODY_SENSE_IDS, SENSE_ID, read_redmi10,
                              read_redmi10_delivery_gaps, read_redmi10_light)
from .multibody import MULTIBODY_SENSE_ID, read_multibody_coverage
from .host_radio import (decode_cam_events, decode_cam_manifest, decode_cam_state,
                        decode_probe_failures, decode_urb_failures)
from . import proc_table
from .space_text import nonblank
from .seed import recorded_session


def _redmi_monitor(home: Path) -> tuple[str, str]:
    """Name stale Redmi delivery only when the peer's structured path is live."""
    reading = read_redmi10(home)
    if not isinstance(reading, dict) or reading.get("id") != SENSE_ID:
        return "unknown", "reader-unavailable"
    channels = reading.get("channels")
    if reading.get("state") == "verified":
        if (isinstance(channels, dict)
                and all(channels.get(kind) == "fresh-clock-conditional"
                        for kind in ("battery", "light", "acceleration"))):
            return "verified", "consumer-current"
        return "unknown", "reader-state-inconsistent"
    # One stale channel is not a whole-body loss; require the full feed to stall.
    stopped = reading.get("reason") == "consumer-poll-stale"
    if (not stopped and reading.get("reason") == "channel-not-current"
            and isinstance(channels, dict)):
        stopped = all(channels.get(kind) == "stale"
                      for kind in ("battery", "light", "acceleration"))
    if not stopped:
        return "unknown", "consumer-not-current"
    peer_status = _tailnet_peer_read()
    if (not isinstance(peer_status, dict)
            or peer_status.get("id") != "sense.net.tailnet-peer"
            or peer_status.get("state") != "verified"):
        return "unknown", "tailnet-state-unavailable"
    peers = peer_status.get("peers")
    peer = peers.get("Redmi 10") if isinstance(peers, dict) else None
    if not isinstance(peer, dict):
        return "unknown", "redmi-peer-unavailable"
    online, active = peer.get("online"), peer.get("active")
    if type(online) is not bool or type(active) is not bool:
        return "unknown", "redmi-peer-state-invalid"
    if online or active:
        return "red", "poll-stale-live-path"
    return "unknown", "poll-stale-peer-not-live"


def _report(home: Path, slug: str, verdict: str) -> None:
    """Publish a verdict atomically: readers see the old report or the new one, never a partial file.

    A producer killed mid-write (render supervisor timeout) leaves the last good report in place,
    so its mtime keeps recording the last successful refresh.
    """
    path = home / "observations" / slug
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(verdict + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


HEALTH_REFRESH_LOCK = "health.refresh.lock"


def refresh_health(home: Path) -> str:
    """Detached producer for observations/health; run under an outer deadline, not from the renderer.

    Single-flight: a second producer that finds the lock held exits as SKIPPED without touching the
    report. Returns the outcome word so callers and receipts can name it.
    """
    lock_path = home / "observations" / HEALTH_REFRESH_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "SKIPPED busy"
        health(home)
        return "REFRESHED"


def _scan_reading(snapshot: dict[str, object]) -> tuple[str, str]:
    current = scan_freshness.endpoint()
    bounds = scan_freshness.age_bounds(snapshot, current) if current is not None else None
    return (scan_freshness.classify_age(bounds, 900),
            scan_freshness.utc_consistency(snapshot))


def _attempt_reading(home: Path, snapshot: dict[str, object] | None) -> tuple[str, str | None]:
    """Classify the durable attempt record against the sample on the pane.

    Only a recorded failure is a scan failure, so it is the only reading that
    flips the STATE line. A ``running`` record is an in-flight scan whose
    sample is not published yet: the pane's sample is the last published
    snapshot, which stays valid, so the STATE line holds and the ATTEMPT line
    names the live stage outside the change digest. A ``succeeded`` record
    with a different ``scan_id`` means the pane read the snapshot before that
    scan published, which scan freshness already covers. An unreadable or
    absent record cannot be checked at all, so it is its own UNKNOWN class
    rather than a silent success.
    """
    attempt = scan_attempt(home)
    if attempt is None:
        return "ATTEMPT: UNKNOWN — no durable scan attempt", "unavailable"
    status = str(attempt.get("status", "unknown"))
    stage = str(attempt.get("stage", "unknown"))
    identity = attempt.get("scan_id")
    matches = snapshot is not None and identity == snapshot.get("scan_id") and isinstance(identity, str)
    line = (f"ATTEMPT: {status.upper()} stage={stage} scan_id={identity or 'unknown'} "
            f"created={attempt.get('created', 'unknown')}")
    if "artifact" in attempt:
        line += f" artifact={attempt['artifact']}"
    if "error" in attempt:
        line += f" error={attempt['error']}"
    if status == "succeeded" and not matches:
        line += " sample_match=unknown"
    if status == "failed":
        return line, "failed"
    if status not in {"running", "succeeded"}:
        return line, "unavailable"
    return line, None


def _journal_bounds_valid(item: dict[str, object]) -> bool:
    coverage = item.get("coverage")
    count = item.get("count")
    if not isinstance(coverage, dict) or type(count) is not int or count < 0:
        return False
    boot = coverage.get("boot_id")
    if not isinstance(boot, str) or re.fullmatch(r"[0-9a-f]{32}", boot) is None:
        return False
    try:
        since = datetime.fromisoformat(coverage["since"].replace("Z", "+00:00"))
        until = datetime.fromisoformat(coverage["until"].replace("Z", "+00:00"))
        return since.utcoffset() is not None and until.utcoffset() is not None and since <= until
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def _clip_sample(sample: str, limit: int = 120) -> str:
    """Shorten a sample for the fixed-width pane without lying about the cut.

    The producers lead a sample with the field the reader needs and expect the
    pane to shorten the tail (``discovery.py`` caps its drift lists for this
    reason). A bare ``sample[:limit]`` cut, though, lands inside a field and
    silently rewrites it — ``all_bad=81`` renders as ``all_bad=8``, which a
    reader cannot tell from a real reading. Cut on the last field boundary
    inside the budget and append an ellipsis, so a shortened sample reads as
    shortened and no field is misstated.
    """
    if len(sample) <= limit:
        return sample
    head = sample[:limit]
    cut = max(head.rfind(" "), head.rfind(","))
    return (head[:cut] if cut > 0 else head) + "…"


def _observation_reading(item: dict[str, object], freshness: str,
                         home: Path | None = None) -> tuple[str, str]:
    source_state = str(item.get("state", "unknown"))
    # A recent scan does not refresh an expired sample: a source that reports
    # its own sample as stale is UNKNOWN for current claims, whatever the scan age.
    fresh_scan = freshness == "recent" and source_state != "stale"
    state = source_state if fresh_scan or source_state == "unavailable" else "unknown"
    identity = str(item.get("id", "unknown"))
    sample = str(item.get("sample", "unknown"))
    if identity == "sense.space.light":
        now = datetime.now(timezone.utc).timestamp()
        current = (_space_light(item["boundary"], now)
                   if "boundary" in item else item)
        if home is not None and current.get("reason") == "light-null-gap":
            # The render-time re-derivation restates the single read from the
            # boundary frame; the duration class is a reader-side fact carried
            # in the retention record, so it is applied again here rather than
            # re-derived and lost.
            current = _space_light_name_freeze(home, current, now)
        bound = current.get("clock_bound")
        if nonblank(bound) is None:
            # Older scans predate the carried bound: fall back to the retained
            # frame, whose light mapping is the reader's own validated copy.
            frame = current.get("boundary", {})
            nodes = frame.get("nodes") if isinstance(frame, dict) else None
            light = (nodes[0].get("light") if isinstance(nodes, list) and nodes
                     and isinstance(nodes[0], dict) else None)
            candidate = light.get("status_reason") if isinstance(light, dict) else None
            bound = nonblank(candidate)
        state = current["state"]
        if "boundary" not in item and not (fresh_scan or source_state == "unavailable"):
            # No retained boundary: the item itself is the reading, so the
            # generic stale gate above must still apply here.
            state = "unknown"
        detail = (f"{current['sample']} source_state={source_state}; "
                  f"display_state={current['state']}; "
                  f"clock={'unverified; ' + bound if bound else 'unverified'}")
        if current.get("event_sample"):
            detail += "; retained history: " + current["event_sample"]
        elif nonblank(current.get("event_reason")) is not None:
            # A transition the reader refused and a producer that published
            # none both leave event_id absent; naming the refusal keeps the
            # pane from reporting the two as the same reading.
            detail += ("; historical transition not retained: "
                       + str(current["event_reason"]))
        return state, f"{state.upper()} {identity}: {detail}"
    if identity.startswith("sense.journal."):
        # Any journal sense carries the same bounds contract: a count over a
        # named boot and window. Trust the contract rather than a fixed id
        # list, so a new journal sense is not silently downgraded to UNKNOWN.
        valid = _journal_bounds_valid(item)
        if not valid and state != "unavailable":
            state = "unknown"
        coverage = item.get("coverage")
        fields = coverage if isinstance(coverage, dict) else {}
        detail = (f"historical sample={sample} fixed-window count={item.get('count', 'unknown')} "
                  f"since={fields.get('since', 'unknown')} until={fields.get('until', 'unknown')} "
                  f"boot={fields.get('boot_id', 'unknown')} source_state={source_state} "
                  f"source_bounds={'valid' if valid else 'unknown'} "
                  f"source_metadata={json.dumps({key: value for key, value in item.items() if key != 'id'}, sort_keys=True)}")
    else:
        detail = f"{_clip_sample(sample)} source_state={source_state}"
    if ((identity.startswith("sense.tmux.") and identity != "sense.tmux.windows")
            or item.get("cached") or item.get("imported")):
        if state != "unavailable":
            state = "unknown"
        detail += " source_freshness=unknown"
    return state, f"{state.upper()} {identity}: {detail}"


def discover(home: Path) -> str:
    lines = ["DESIRED STATE: useful reads have a real sample, honest status, and an owned next step"]
    snapshot = latest(home)
    attempt_line, attempt_failure = _attempt_reading(home, snapshot)
    lines.append(attempt_line)
    if snapshot is None:
        lines.append("STATE: UNKNOWN — no discovery scan yet")
        verdict = "UNKNOWN discover has no scan"
    else:
        freshness, consistency = _scan_reading(snapshot)
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={freshness} "
                     f"utc_consistency={consistency} producer={producer_label(home, snapshot.get('producer'))}")
        observations = snapshot.get("observations", [])
        observations = [item for item in observations if item.get("id") != "sense.space.light"]
        observations.append(_space_light_read(home))
        unknown = 0
        drift = 0
        settled = 0
        for item in observations:
            state, line = _observation_reading(item, freshness, home)
            if state == "drift":
                drift += 1
            elif state == "settled":
                # A settled reading is checked: it reports that its history is
                # accounted for and that nothing is owed. Counting it as
                # unchecked would make this pane say UNKNOWN about a sample
                # the senses pane calls a checked read.
                settled += 1
            elif state not in {"verified", "unavailable", "available"}:
                unknown += 1
            lines.append(line)
        if freshness != "recent":
            lines.append(f"STATE: UNKNOWN — scan freshness {freshness}; renew the read")
            verdict = f"UNKNOWN discover scan freshness {freshness}"
        elif attempt_failure == "failed":
            lines.append("STATE: UNKNOWN — latest scan attempt failed for this sample")
            verdict = "UNKNOWN discover latest attempt failed"
        elif attempt_failure is not None:
            lines.append("STATE: UNKNOWN — scan attempt record unavailable")
            verdict = "UNKNOWN discover scan attempt record unavailable"
        elif unknown or drift:
            clauses = []
            if unknown:
                clauses.append(f"{unknown} source reads unverified")
            if drift:
                clauses.append(
                    f"{drift} checked reading{'s' if drift != 1 else ''} "
                    f"report{'s' if drift == 1 else ''} drift")
            summary = "; ".join(clauses)
            lines.append(f"STATE: UNKNOWN — {summary}")
            verdict = f"UNKNOWN discover {summary}"
        else:
            lines.append("STATE: GREEN — recent bounded read-only scan")
            verdict = "PASS discover recent scan"
    retired = retired_requests(home)
    pending = [(item, state) for item, state in list_requests(home)
               if state == "pending" and item.identity not in retired]
    lines.append(f"PERMISSION REQUESTS: {len(pending)} pending")
    for item, _ in pending[-5:]:
        lines.append(f"REQUEST {item.identity} unblocks={','.join(item.unblocks)}")
    lines.extend(["GOAL: discover useful new directions for Mishe and this plant's goals",
                  "PURSUIT: explore capabilities and crossed senses; use related literature when useful, assess fit, and check ideas",
                  "NEXT: renew an absent, stale, or unknown scan; otherwise choose one observation, reading, or experiment and record its outcome"])
    _report(home, "discover", verdict)
    return "\n".join(lines) + "\n"


def senses(home: Path) -> str:
    lines = ["GOAL: turn readings into honest senses with real samples and visible unknowns",
             "PURSUIT: wire useful reads, reproduce hollow or failed checks, and own reusable fixes through review, branch CI and ready integration",
             "NEXT: pursue a new useful read when an UNKNOWN has a documented retry condition; verify its live sample"]
    snapshot = latest(home)
    attempt_line, attempt_failure = _attempt_reading(home, snapshot)
    redmi = read_redmi10(home)
    redmi_light = read_redmi10_light(home)
    delivery_gaps = read_redmi10_delivery_gaps(home)
    multibody = read_multibody_coverage(home)
    lines.append(attempt_line)
    if snapshot is None:
        verdict = "UNKNOWN senses no discovery sample"
        lines.append("STATE: UNKNOWN — no sample exists")
        for clock_row in _space_clock_domains_read(home):
            state, line = _observation_reading(clock_row, "recent", home)
            lines.append(line)
        _, redmi_line = _observation_reading(redmi, "recent", home)
        lines.append(redmi_line)
        if isinstance(redmi.get("details"), str):
            lines.append("REDMI10 SOURCE/LIMITS: " + redmi["details"])
        for body_row in (redmi_light, delivery_gaps):
            _, body_line = _observation_reading(body_row, "recent", home)
            lines.append(body_line)
        _, multibody_line = _observation_reading(multibody, "recent", home)
        lines.append(multibody_line)
        if isinstance(multibody.get("details"), str):
            lines.append("MULTIBODY SOURCE/LIMITS: " + multibody["details"])
    else:
        freshness, consistency = _scan_reading(snapshot)
        # The carried-body rows are re-read here rather than taken from the
        # scan: the scan runs on its own cadence, so a snapshot copy would age
        # behind the pane and render a second, disagreeing copy of a row the
        # pane already reads live.
        senses_rows = [item for item in snapshot.get("observations", [])
                       if str(item.get("id", "")).startswith("sense.")
                       and item.get("id") not in {"sense.space.light", *BODY_SENSE_IDS,
                                                  MULTIBODY_SENSE_ID}]
        space = _space_light_read(home)
        senses_rows.append(redmi)
        senses_rows.append(redmi_light)
        senses_rows.append(delivery_gaps)
        senses_rows.append(multibody)
        senses_rows.append(space)
        senses_rows.extend(_space_clock_domains_read(home))
        unknown = 0
        unavailable = 0
        drift = 0
        settled = 0
        for item in senses_rows:
            # The carried-body rows are re-read at render time, so the scan's
            # own freshness does not age them: a stale scan must not turn a
            # just-taken live read into a second UNKNOWN.
            item_freshness = ("recent"
                              if item.get("id") in {*BODY_SENSE_IDS, MULTIBODY_SENSE_ID}
                              else freshness)
            state, line = _observation_reading(item, item_freshness, home)
            # A drift row is a checked read whose own value is a drift state,
            # not a read that never happened. Counting it as unchecked made the
            # pane claim four readings needed a read while it printed those four
            # as DRIFT on the same frame. A settled row is checked too: it
            # reports that its history is accounted for and that nothing is
            # owed, which is neither drift nor a clean reading, so it is
            # counted apart rather than as unchecked.
            if state == "drift":
                drift += 1
            elif state == "settled":
                settled += 1
            elif state not in {"verified", "unavailable"}:
                unknown += 1
            unavailable += state == "unavailable"
            lines.append(line)
            if isinstance(item.get("details"), str):
                label = ("MULTIBODY SOURCE/LIMITS: "
                         if item.get("id") == MULTIBODY_SENSE_ID
                         else "REDMI10 SOURCE/LIMITS: ")
                lines.append(label + item["details"])
        provenance = space.get("provenance", {})
        clock_bound = nonblank(space.get("clock_bound"))
        clock_text = (clock_bound
                      if clock_bound is not None else "phone/host clock agreement unverified")
        event_id = space.get("event_id")
        event_text = f"retained_historical_event={event_id or 'none'}"
        if nonblank(space.get("event_reason")) is not None and event_id is None:
            event_text += f" event_reason={space['event_reason']}"
        lines.append(f"UNKNOWN SPACE: {clock_text}; "
                     f"boundary_state={space['state']} reason={space['reason']} "
                     f"provenance={provenance.get('source', 'unknown')} "
                     f"session={provenance.get('session', 'unknown')} "
                     f"{event_text}; "
                     "baseline/restart/recovery are not physical transitions")
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={freshness} "
                     f"utc_consistency={consistency} producer={producer_label(home, snapshot.get('producer'))}")
        if freshness != "recent":
            verdict = f"UNKNOWN senses scan freshness {freshness}"
            lines.append(f"STATE: UNKNOWN — scan freshness {freshness}; renew the read")
        elif attempt_failure == "failed":
            verdict = "UNKNOWN senses latest attempt failed"
            lines.append("STATE: UNKNOWN — latest scan attempt failed for this sample")
        elif attempt_failure is not None:
            verdict = "UNKNOWN senses scan attempt record unavailable"
            lines.append("STATE: UNKNOWN — scan attempt record unavailable")
        elif unknown or drift:
            claims = []
            if unknown:
                claims.append(f"{unknown} senses need a checked read or honest unavailable claim")
            if drift:
                claims.append(f"{drift} checked readings report drift")
            if not unknown:
                # Every row was read: the pane is not green because those reads
                # report drift. Saying so keeps the STATE line truthful, and the
                # UNKNOWN token stays because it is what routes a drift to the
                # mind's notification path.
                claims.append("no reading is unchecked")
            lines.append("STATE: UNKNOWN — " + "; ".join(claims))
            verdict = "UNKNOWN senses " + "; ".join(
                ([f"{unknown} unverified"] if unknown else []) +
                ([f"{drift} report drift"] if drift else []))
        elif settled:
            # The reads are all checked, but one of them reports an accounted
            # history rather than a clean reading. Saying "all verified" here
            # would claim exactly what the settled word exists not to claim.
            verdict = f"PASS senses {len(senses_rows)} checked samples ({settled} settled)"
            lines.append(f"STATE: GREEN — all wired sample reads checked "
                         f"({settled} settled, not verified)")
        else:
            verdict = f"PASS senses {len(senses_rows)} verified samples"
            lines.append("STATE: GREEN — all wired sample reads verified")
        if settled:
            # A settled reading is checked and reports that its history is
            # accounted for, so it is neither drift nor an unchecked read.
            # Saying so keeps the STATE line truthful: the pane is green about
            # the reads, and the settled history is named wherever it appears.
            lines.append(f"SETTLED senses {settled} reading(s) report accounted "
                         "history, not a clean reading")
        if unavailable:
            lines.append(f"UNAVAILABLE senses {unavailable} named an absent source, not a failed read")
    _report(home, "senses", verdict)
    return "\n".join(lines) + "\n"

def _service_readings(units: list[str], env: dict[str, str]) -> tuple[dict[str, tuple[str, str, int | None]], dict[str, list[str]]]:
    """One coherent systemctl sample for every listed unit, keyed by unit name.

    Per-unit ``is-active`` loops sample different instants, so a unit that crashes
    and is auto-restarted between two calls reads healthy each time: the restart
    window and the durable ``NRestarts`` counter never reach the pane. A failed
    read yields no readings, leaving every unit unknown rather than fabricating a
    healthy state. The same sample carries each unit's import roots so the caller
    can compare them with the pin without a second probe.
    """
    if not units:
        return {}, {}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", *units,
             "-p", "Id,ActiveState,SubState,NRestarts,Environment"],
            capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return {}, {}
    if result.returncode:
        return {}, {}
    from .wall_view import _import_roots
    readings: dict[str, tuple[str, str, int | None]] = {}
    roots: dict[str, list[str]] = {}
    for block in result.stdout.strip().split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        name = values.get("Id")
        if not name:
            continue
        count = values.get("NRestarts", "")
        readings[name] = (values.get("ActiveState", "unknown"), values.get("SubState", "unknown"),
                          int(count) if count.isdigit() else None)
        unit_roots = _import_roots(values.get("Environment", ""))
        if unit_roots:
            roots[name] = unit_roots
    return readings, roots



# The camera's fault surface has no detector of its own: mesh-cam-watch keeps a
# class line in ~/.mesh/.cam-watch.state, and the UVC function can stall
# intermittently (v4l2 read timeouts, kernel URB resubmit failures) with nothing
# reading them. The bounds are the producers' own contracts: 120s is the
# reflex-artifact age mesh-cam-watch declares for cam-prev.jpg, and a fault
# repeated inside a 15-minute window is a fault rather than one self-healed
# cycle. A node declares its camera with health/camera.json, so a plant without
# a webcam skips the check instead of inheriting this node's surface; unreadable
# sources on a declared node read UNKNOWN so the observation never silently
# degrades to PASS.
CAMERA_ARTIFACT_BOUND_S = 120.0
CAMERA_WINDOW_S = 900.0
CAMERA_CAPTURE_FAULTS = 3
CAMERA_KERNEL_FAULTS = 10
CAMERA_KERNEL_PROBE_FAULTS = 10
CAMERA_MANIFEST = "camera.json"
_CAMERA_EVENTS_TAIL_B = 1 << 18
CLEAR_JOURNAL_STALL_S = 60.0
"""Age after which a clear journal stuck between rotating and rotated is a stall.

Half the witness CLEAR STALL budget (120 s): a rotation normally commits inside
one supervisor tick, so a journal still mid-rotation after a minute means the
receipt guard keeps rejecting the live pane — the tmux-resurrection pid class —
and the supervisor is holding in a silent loop. Surfacing the stuck phase here
names the cause while the generic overdue-clear RED is still pending.
"""


def _camera_faults(home: Path, env: dict[str, str]) -> tuple[str, str, list[str]]:
    """Read the node-local camera fault surface.

    Returns the pane line, an UNKNOWN reason ("" when the sources read), and the
    RED causes. A node that declares no camera (no ``health/camera.json``) is
    skipped, not failed — a plant without a webcam must not inherit its
    neighbor's fault surface. A declared manifest that cannot be read reads
    UNKNOWN, so deleted monitoring stays visible instead of silent. The class
    word from .cam-watch.state is informational: a covered lens is not a fault,
    and the line must not imply one. The kernel journal is counted in two
    distinct camera classes — URB resubmit failures and UVC probe-control
    failures — because a wedged device logs the probe class alone (health
    2026-10-20: 1195 ``Failed to set UVC probe control : -110`` lines with zero
    resubmit lines, while this surface reported ``kernel-urbs=0`` and named no
    kernel cause). Each class is counted and named separately.
    A kernel journal that cannot be read — absent, or past its five-second
    budget — stays UNKNOWN and names no kernel cause: both counters read the
    same result, so a failed read must leave the pane UNKNOWN instead of
    raising out of the view.
    """
    try:
        declared = decode_cam_manifest(
            (home / "health" / CAMERA_MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "", "", []
    except OSError:
        return "CAMERA: UNKNOWN — camera manifest unreadable", "camera manifest unreadable", []
    if not declared:
        # A present manifest that is not a supported v1 declaration is monitoring
        # drift, not absence: UNKNOWN, never a silent skip.
        return ("CAMERA: UNKNOWN — camera manifest unsupported",
                "camera manifest unsupported", [])
    mesh_dir = Path.home() / ".mesh"
    if not mesh_dir.is_dir():
        return ("CAMERA: UNKNOWN — camera node without mesh artifacts",
                "camera mesh artifacts missing", [])
    causes: list[str] = []
    unknown = ""
    try:
        klass = decode_cam_state((mesh_dir / ".cam-watch.state").read_text(
            encoding="utf-8", errors="replace"))
    except OSError:
        klass = None
        unknown = "camera state unreadable"
    now = datetime.now(timezone.utc).timestamp()
    try:
        age_s = max(0.0, now - (mesh_dir / "cam-prev.jpg").stat().st_mtime)
    except OSError:
        age_s = None
        causes.append("camera-artifact-stale")
        unknown = unknown or "camera artifact missing"
    else:
        if age_s > CAMERA_ARTIFACT_BOUND_S:
            causes.append("camera-artifact-stale")
    try:
        with (mesh_dir / "cam-events.log").open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - _CAMERA_EVENTS_TAIL_B))
            rows = decode_cam_events(stream.read())
    except OSError:
        rows = []
        unknown = unknown or "camera events unreadable"
    onsets = sum(1 for epoch, token in rows
                 if token == "CAPTURE-FAIL" and 0 <= now - epoch <= CAMERA_WINDOW_S)
    if onsets >= CAMERA_CAPTURE_FAULTS:
        causes.append(f"camera-capture-fail={onsets}")
    kernel = 0
    probe = 0
    since = datetime.fromtimestamp(now - CAMERA_WINDOW_S, timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")
    try:
        result = subprocess.run(["journalctl", "-k", "--since", since, "--no-pager"],
                                capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        result = None
        unknown = unknown or "camera kernel log unreadable"
    if result is not None:
        if result.returncode == 0:
            kernel = decode_urb_failures(result.stdout)
            probe = decode_probe_failures(result.stdout)
        else:
            unknown = unknown or "camera kernel log unreadable"
    if kernel >= CAMERA_KERNEL_FAULTS:
        causes.append(f"camera-kernel-urb={kernel}")
    if probe >= CAMERA_KERNEL_PROBE_FAULTS:
        causes.append(f"camera-kernel-probe={probe}")
    if causes:
        state = "RED"
    elif unknown:
        state = "UNKNOWN"
    else:
        state = "PASS"
    age_text = "missing" if age_s is None else f"{age_s:.0f}s"
    line = (f"CAMERA: {state} " +
            (f"class={klass} " if klass else "") +
            f"artifact-age={age_text} capture-fails={onsets}/{CAMERA_WINDOW_S:.0f}s "
            f"kernel-urbs={kernel}/{CAMERA_WINDOW_S:.0f}s "
            f"kernel-probes={probe}/{CAMERA_WINDOW_S:.0f}s")
    if state == "UNKNOWN":
        line += f" — {unknown}"
    return line, unknown, causes


def _clear_journal_stalls(home: Path) -> tuple[str, list[str]]:
    """Flag clear journals abandoned mid-rotation past the stall budget.

    `wall.clear` records each rotation under `checks/wall-clear-*.json` and
    walks phase rotating -> rotated -> committed within one supervisor tick.
    A journal that stops in rotating or rotated means its receipt guard keeps
    rejecting the live pane — the tmux-resurrection pid class from witness DM
    16442 — and the supervisor holds the clear in a silent loop until the
    witness 120 s budget turns the generic overdue-clear RED. Naming the stuck
    phase here gives the resolver the role, the wake and the cause one budget
    earlier.

    A committed journal naming the role's *current* settled turn is the second
    silent hold, and the one this line could not see: `clear` writes that phase
    only after the feed records the turn's own clear, so one naming a turn the
    feed has not cleared cannot describe this rotation — the feed was
    renumbered and the settled number collided with a journal from an earlier
    numbering. The guards of a runtime without the retire path then reject
    every retry, forever (witness settled=39741 held 09:47-15:23Z 2026-10-14
    against an Oct-6 committed journal). Only the current settled turn is
    judged, so the committed journals of earlier turns stay inert history
    instead of turning this line red.

    A journal that cannot be read is monitoring damage and stays UNKNOWN rather
    than silently passing; a healthy journal is simply absent from the concern
    set, so an empty directory reads PASS.

    Boundedness: the checks directory can accumulate tens of thousands of
    wall-clear journals (13553 measured 2026-10-29). Reading every body and
    parsing the feed per committed file exceeds the pane's 30s probe budget
    (doctor 7s + full scan 12s per role). Filter by mtime first (stat only),
    pre-load the feed once, and only judge committed journals whose settled
    equals the role's current last_yield.
    """
    from . import seed
    from .feed import Feed
    stuck: list[str] = []
    collisions: list[str] = []
    unknown = ""
    now = datetime.now(timezone.utc).timestamp()
    try:
        rows = clear_journal.paths(home)
    except OSError:
        return "CLEAR JOURNAL: UNKNOWN — checks directory unreadable", []
    # Stat-filter before any JSON decode: mtime read is ~0.04s for 13k files
    # versus 4.7s for full read+stat.
    old_paths: list[Path] = []
    old_set: set[Path] = set()
    for path in rows:
        try:
            age = max(0.0, now - path.stat().st_mtime)
        except OSError:
            unknown = unknown or "journal mtime unreadable"
            continue
        if age < CLEAR_JOURNAL_STALL_S:
            continue
        old_paths.append(path)
        old_set.add(path)
    # FAST-PASS sentinel: only when no old journal exists we can avoid
    # reading any body, but monitoring damage (unreadable JSON) in a fresh
    # file must still turn the line UNKNOWN, not PASS. Detect that case
    # before returning, and likewise surface a fresh corrupt neighbour next
    # to a RED stuck journal (test_clear_journal_stall_detector_names_stuck_rotation).
    if not old_paths:
        # No candidate for stuck/collision — PASS only if every journal
        # (including fresh) is readable. Scan fresh set for damage.
        has_unreadable = False
        for path in rows:
            # rows already known not old; reading is bounded to the live set.
            journal = clear_journal.read(path)
            if journal is None:
                has_unreadable = True
                unknown = unknown or "journal unreadable"
                break
        if has_unreadable or unknown:
            return f"CLEAR JOURNAL: UNKNOWN — {unknown or 'journal unreadable'}", []
        return "CLEAR JOURNAL: PASS", []
    # One feed parse for all roles; reuse entries across _state calls.
    turn_by_role: dict[str, tuple[object, object]] = {}
    try:
        entries = Feed(home).entries()
    except Exception:
        entries = None
    # Distinct roles from filenames avoid decoding every body just to learn role.
    # Decoding lives in the adapter: clear_journal.role_from_filename is the
    # boundary for this filename grammar (parsing-policy.json).
    seen_roles: set[str] = set()
    for path in old_paths:
        role = clear_journal.role_from_filename(path.name)
        if role is None:
            continue
        if role not in seen_roles:
            seen_roles.add(role)
            if role not in turn_by_role:
                try:
                    _, _, last_yield, last_clear, *_ = seed._state(home, role, entries=entries)
                    turn_by_role[role] = (last_yield, last_clear)
                except Exception:
                    turn_by_role[role] = (None, None)
    for path in old_paths:
        journal = clear_journal.read(path)
        if journal is None:
            unknown = unknown or "journal unreadable"
            continue
        phase = journal.get("phase")
        if phase not in {"rotating", "rotated", "committed"}:
            continue
        role = journal.get("role")
        settled = journal.get("settled")
        if phase in {"rotating", "rotated"}:
            # Recompute age already known, but keep bounded formatting
            try:
                age = max(0.0, now - path.stat().st_mtime)
            except OSError:
                age = 0.0
            stuck.append(f"{role}/{settled}:{phase}:{age:.0f}s")
            continue
        if (not isinstance(role, str) or not isinstance(settled, int)
                or isinstance(settled, bool)):
            continue
        last_yield, last_clear = turn_by_role.get(role, (None, None))
        if settled == last_yield and last_clear != settled:
            try:
                age = max(0.0, now - path.stat().st_mtime)
            except OSError:
                age = 0.0
            collisions.append(f"{role}/{settled}:committed-collision:{age:.0f}s")
    if stuck or collisions:
        # Stuck names the cause; a fresh corrupt neighbour is still
        # monitoring damage and must be surfaced alongside the RED.
        if not unknown:
            for path in rows:
                if path in old_set:
                    continue
                journal = clear_journal.read(path)
                if journal is None:
                    unknown = "journal unreadable"
                    break
        line = (f"CLEAR JOURNAL: RED stuck={','.join(stuck + collisions)} "
                f"budget={CLEAR_JOURNAL_STALL_S:.0f}s")
        if unknown:
            # A stuck journal names the cause; an unreadable neighbour is
            # monitoring damage that must stay visible next to it, not be
            # subsumed by the red.
            line += f" — {unknown}"
        causes = [f"clear-journal-stuck={len(stuck)}"] if stuck else []
        if collisions:
            causes.append(f"clear-journal-collision={len(collisions)}")
        return line, causes
    if unknown:
        return f"CLEAR JOURNAL: UNKNOWN — {unknown}", []
    # No stuck/collision and no damage among old journals — a fresh
    # unreadable file is still damage, not PASS.
    for path in rows:
        if path in old_set:
            continue
        journal = clear_journal.read(path)
        if journal is None:
            return "CLEAR JOURNAL: UNKNOWN — journal unreadable", []
    return "CLEAR JOURNAL: PASS", []


def _pane_roots(home: Path, pane_rows: list[tuple[str, str, str, str]]) -> tuple[str, str, list[str]]:
    """Compare every pane's live import root with the site pin.

    A mind pane's byte-set is the pin as of the launcher that raised it, so a
    promotion leaves panes importing superseded bytes with nothing reading
    their root: the runtime sensor reads unit environments and the lease sensor
    reads renderer panes, and a mind pane inherits neither. A root equal to
    the pin is on the pin; a root under this site's releases directory is a
    promotion the pane has not rotated onto yet, which reads stale and stays
    visible without raising a state change, because a rotation is the owner's
    own wake. Any other root — the development checkout, a foreign tree — is
    drift the state line must name. A live mind pane whose environment reads
    no root is missing evidence, not a healthy pane, so it reads UNKNOWN, and
    that reading outranks every classified one: an unobserved pane must not
    hide behind a classified neighbour. The first PYTHONPATH component
    decides, as every launcher here exports a single root. A dead pane is the
    WINDOWS check's. A pane whose process is not a mind — an operator shell,
    a log tail, an interactive bash left in a window — carries no root to
    compare whether or not a launcher exported PYTHONPATH into its environment:
    the pane's cmdline decides, not its environ. A site without a pin has no pinned
    byte-set to compare against and skips the check, the way a camera-less
    node skips the camera line.
    """
    if not (home / "health" / "runtime-release.json").exists():
        return "", "", []
    if not pane_rows:
        # No pane sample is no observation: the WINDOWS check names the read
        # failure, and this check must not read PASS from an empty sample.
        return "PANE ROOTS: UNKNOWN — pane sample empty", "pane sample empty", []
    try:
        from .runtime_source import source_for
        pinned = source_for(home, Path(__file__).resolve().parents[2])
    except (OSError, TypeError, ValueError) as exc:
        return f"PANE ROOTS: UNKNOWN — pin unreadable: {exc}", "pane root pin unreadable", []
    releases = (home / "releases").resolve()
    roots: dict[str, list[str]] = {}
    unread: list[str] = []
    for window, _index, state, pid in pane_rows:
        if state != "0":
            continue
        if "mishe_tauftauf" not in proc_table.read_pane_cmdline(pid):
            # Not a mind pane (operator shell, log tail, interactive bash): no
            # byte-set to compare, even when its environ carries a PYTHONPATH.
            continue
        components = proc_table.read_pane_pythonpath(pid)
        if not components:
            # A mind launcher exports PYTHONPATH for the pane it raises, so a
            # mind window reading no root is missing evidence rather than a
            # healthy pane.
            if (home / "minds" / window).is_file():
                unread.append(window)
            continue
        roots.setdefault(window, []).append(proc_table.pane_root(components[0]))
    on_pin = 0
    stale: list[str] = []
    drift: list[str] = []
    for window, window_roots in sorted(roots.items()):
        divergent = sorted({item for item in window_roots if Path(item).resolve() != pinned})
        if not divergent:
            on_pin += 1
            continue
        for root in divergent:
            root_path = Path(root).resolve()
            entry = f"{window}:{root_path.name[:12]}" if root_path.parent == releases else f"{window}:{root}"
            if root_path.parent == releases:
                stale.append(entry)
            else:
                drift.append(entry)
    pin_text = pinned.name[:12]
    if unread:
        return (f"PANE ROOTS: UNKNOWN — pane environments unreadable "
                f"unread={','.join(sorted(set(unread)))}", "pane environments unreadable", [])
    if drift:
        line = f"PANE ROOTS: DRIFT pin={pin_text} on-pin={on_pin} drift={','.join(drift)}"
        if stale:
            line += f" stale={','.join(stale)}"
        return line, "", ["pane-roots-drift=" + ",".join(drift)]
    if stale:
        return (f"PANE ROOTS: STALE pin={pin_text} on-pin={on_pin} "
                f"stale={','.join(stale)}", "", [])
    return f"PANE ROOTS: PASS pin={pin_text} on-pin={on_pin}", "", []


def health(home: Path) -> str:
    lines = ["GOAL: keep this plant's panes, feed, and resident services working",
             "PURSUIT: repair internal failures from the live check; own reusable code delivery through ready integration",
             "NEXT: investigate the first RED or UNKNOWN internal check and verify its live recovery"]
    try:
        doctor = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "doctor"],
                                capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        # Doctor hung past the probe budget — report as UNKNOWN timeout rather
        # than crashing the whole health view (a47e7a4 class, doctor 8-15s).
        lines.append("DOCTOR: UNKNOWN — probe timeout 25s")
        doctor = None
    else:
        lines.append("DOCTOR: " + ("PASS" if doctor.returncode == 0 else "RED"))
        if doctor.returncode:
            lines.extend(doctor.stdout.splitlines()[-8:])
    session = os.environ.get("MISHE_SEED_SESSION", "") or recorded_session(home) or ""
    expected_path = home / "health" / "windows.json"
    try:
        expected = set(json.loads(expected_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        expected = set()
    windows_unknown = ""
    redmi_unknown = ""
    redmi_status: str | None = None
    redmi_detail = ""
    missing = extra = dead = []
    pane_rows: list[tuple[str, str, str, str]] = []
    if not session:
        windows_unknown = "session unset"
        lines.append("WINDOWS: UNKNOWN — session unset")
    else:
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-s", "-t", session,
                 "-F", "#{window_name} #{pane_index} #{pane_dead} #{pane_pid}"],
                capture_output=True, text=True, timeout=2)
            read_ok = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            read_ok = False
        if not read_ok:
            # A failed tmux read is no observation: name the read failure
            # (UNKNOWN) instead of claiming every window is missing (RED). A
            # genuinely dead server still reads RED through the DOCTOR check.
            windows_unknown = "pane read failed"
            lines.append("WINDOWS: UNKNOWN — pane read failed")
        else:
            pane_states = {}
            for window, index, state, _pid in proc_table.pane_windows(result.stdout):
                pane_states[(window, index)] = state
                pane_rows.append((window, index, state, _pid))
            actual = {name for name, _ in pane_states}
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            dead = [f"{name}.0" for name in expected & actual
                    if pane_states.get((name, "0")) != "0"]
            # A chartered mind's bottom pane can die at status 127 while its
            # renderer (.0) stays live: `remain-on-exit` keeps the corpse visible, so
            # the renderer lease cannot see it. `doctor --panes` checks this, but
            # nothing runs that periodically, and one busy peer masks a wedged mind
            # on the global activity line. Detect it from the pane snapshot already
            # taken, for windows that ship a mind launcher.
            dead += [f"{name}.1" for name in expected & actual
                     if (home / "minds" / name).is_file()
                     and pane_states.get((name, "1"), "0") != "0"]
            dead = sorted(dead)
            lines.append("WINDOWS: " + ("PASS " if expected and not missing and not extra and not dead else "RED ") +
                         ",".join(sorted(actual)) + (" missing=" + ",".join(missing) if missing else "") +
                         (" dead=" + ",".join(dead) if dead else "") +
                         (" extra=" + ",".join(extra) if extra else ""))
    services_path = home / "health" / "services.json"
    local_services_unknown = False
    try:
        services = json.loads(services_path.read_text(encoding="utf-8"))
        if (not isinstance(services, list) or len(services) > 32
                or any(not isinstance(unit, str) or not unit.isprintable()
                       or not unit.endswith(".service") or unit.startswith("-")
                       or "/" in unit or any(c.isspace() for c in unit)
                       for unit in services)):
            raise ValueError("invalid local services list")
    except (OSError, ValueError, TypeError):
        services = []
        local_services_unknown = True
        lines.append("SERVICES: UNKNOWN — local manifest unavailable or malformed")
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    restarts_path = home / "health" / "service-restarts.json"
    try:
        previous_restarts = json.loads(restarts_path.read_text(encoding="utf-8"))
        if not isinstance(previous_restarts, dict):
            previous_restarts = {}
    except (OSError, ValueError):
        previous_restarts = {}
    readings, service_roots = _service_readings(services, env)
    failed_services = []
    current_restarts: dict[str, int] = {}
    for unit in services:
        active, sub, restarts = readings.get(unit, ("unknown", "unknown", None))
        baseline = previous_restarts.get(unit)
        restarted = (isinstance(restarts, int) and isinstance(baseline, int)
                     and not isinstance(baseline, bool) and restarts > baseline)
        if isinstance(restarts, int):
            current_restarts[unit] = restarts
        lines.append(f"SERVICE {unit}: {active}/{sub}" +
                     (f" restarts={restarts}" if isinstance(restarts, int) else ""))
        if active != "active" or sub == "auto-restart" or restarted:
            failed_services.append(unit)
    if current_restarts:
        try:
            restarts_path.parent.mkdir(parents=True, exist_ok=True)
            restarts_path.write_text(json.dumps(current_restarts, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    camera_line, camera_unknown, camera_fault_causes = _camera_faults(home, env)
    if camera_line:
        lines.append(camera_line)
    clear_journal_line, clear_journal_causes = _clear_journal_stalls(home)
    lines.append(clear_journal_line)
    pane_roots_line, pane_roots_unknown, pane_roots_causes = _pane_roots(home, pane_rows)
    if pane_roots_line:
        lines.append(pane_roots_line)
    linked_unknown = []
    linked_failed = []
    linked_path = home / "health" / "linked-sites.json"
    try:
        registry = json.loads(linked_path.read_text(encoding="utf-8"))
        sites = registry["sites"] if isinstance(registry, dict) and registry.get("version") == 1 else None
        if not isinstance(sites, list) or len(sites) > 16:
            raise ValueError("unsupported or oversized linked-site registry")
    except FileNotFoundError:
        # External plants have no coordinator or linked-site registry.
        sites = []
    except (OSError, ValueError, KeyError, TypeError):
        sites = None
        linked_unknown.append("registry")
        lines.append("LINKED SITES: UNKNOWN — registry unavailable or malformed")
    deadline = monotonic() + 5
    if sites is not None:
        for index, site in enumerate(sites):
            label = site.get("session") if isinstance(site, dict) else None
            if not isinstance(label, str) or not label.isprintable():
                label = str(index)
            try:
                if (not isinstance(site, dict) or not isinstance(site.get("home"), str)
                        or not Path(site["home"]).is_absolute()
                        or not isinstance(site.get("session"), str) or not site["session"].isprintable()):
                    raise ValueError("invalid site record")
                site_services = json.loads(
                    (Path(site["home"]) / "health" / "services.json").read_text(encoding="utf-8"))
                if (not isinstance(site_services, list) or len(site_services) > 32
                        or any(not isinstance(unit, str) or not unit.isprintable()
                               or not unit.endswith(".service")
                               or unit.startswith("-") or "/" in unit or any(c.isspace() for c in unit)
                               for unit in site_services)):
                    raise ValueError("invalid services list")
            except (OSError, ValueError, TypeError, KeyError):
                lines.append(f"LINKED SITE {label}: UNKNOWN — services unavailable or malformed")
                linked_unknown.append(label)
                continue
            site_failed = []
            site_unknown = []
            for unit in site_services:
                try:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        status = "unknown"
                    else:
                        result = subprocess.run(
                            ["systemctl", "--user", "is-active", unit], capture_output=True,
                            text=True, timeout=min(2, remaining), env=env)
                        status = result.stdout.strip()
                        if (status not in {"active", "inactive", "failed", "activating",
                                           "deactivating", "reloading", "maintenance"}
                                or (status == "active" and result.returncode != 0)):
                            status = "unknown"
                except (OSError, subprocess.TimeoutExpired):
                    status = "unknown"
                lines.append(f"LINKED SERVICE {label} {unit}: {status}")
                if status == "unknown":
                    site_unknown.append(unit)
                elif status != "active":
                    site_failed.append(unit)
            if site_failed:
                linked_failed.append(label)
                lines.append(f"LINKED SITE {label}: RED — inactive services={','.join(site_failed)}")
            elif site_unknown:
                linked_unknown.append(label)
                lines.append(f"LINKED SITE {label}: UNKNOWN — service status unavailable")
            else:
                lines.append(f"LINKED SITE {label}: PASS")
    android_sites = ([site for site in sites
                      if isinstance(site, dict) and site.get("session") == "android-body"]
                     if sites is not None else [])
    if len(android_sites) == 1:
        redmi_status, redmi_detail = _redmi_monitor(home)
        lines.append(f"REDMI10 MONITOR: {redmi_status.upper()} {redmi_detail}")
    elif len(android_sites) > 1:
        redmi_status, redmi_detail = "unknown", "linked-site-ambiguous"
        lines.append(f"REDMI10 MONITOR: UNKNOWN {redmi_detail}")
    lines.append(ci_line(home))
    from .wall_view import pane_lease_lines
    lease_lines, lease_state = pane_lease_lines(home)
    lines.extend(lease_lines)
    causes: list[str] = []
    causes.extend(camera_fault_causes)
    causes.extend(clear_journal_causes)
    causes.extend(pane_roots_causes)
    if doctor.returncode:
        causes.append("doctor")
    if missing:
        causes.append("windows-missing=" + ",".join(missing))
    if extra:
        causes.append("windows-extra=" + ",".join(extra))
    if dead:
        causes.append("windows-dead=" + ",".join(dead))
    if failed_services:
        causes.append("services=" + ",".join(failed_services))
    if redmi_status == "red":
        causes.append("redmi10-" + redmi_detail)
    elif redmi_status == "unknown":
        redmi_unknown = redmi_detail
    # Fold the runtime import-root check in, so a latched RED from the pane's
    # STATE line is also named in the durable report. The pane's own RUNTIME
    # line reads DRIFT when a covered service imports another root; an invalid
    # or absent pin reads UNKNOWN/UNPINNED and is not itself a fault.
    from .wall_view import _runtime_state
    runtime_state, _ = _runtime_state(home, Path(__file__).resolve().parents[2], service_roots)
    if runtime_state == "DRIFT":
        causes.append("runtime-drift")
    # The renderer-bytes clause reads each renderer's own import root. An
    # unreadable renderer is UNKNOWN there while this verdict would still read
    # GREEN, so a resolver scanning only the line below sees a silently green
    # pane. Fold the same state in (research-methods DM 61672, disposition (a)).
    # Empty lines mean there is no renderer window to observe, not an unobserved
    # one; they must not turn a healthy site UNKNOWN.
    bytes_lines: list[str] = []
    bytes_state = ""
    bytes_error = ""
    try:
        from .wall_view import renderer_bytes_lines
        bytes_lines, bytes_state = renderer_bytes_lines(home)
    except (OSError, ValueError, AttributeError) as exc:
        bytes_error = str(exc) or exc.__class__.__name__
    if bytes_state == "DRIFT" and bytes_lines:
        causes.append("renderer-bytes=" + _clip_sample(bytes_lines[0], 160))
    # A dead tmux server or an unshielded one is a host failure Health owns:
    # the guard loop re-shields within a minute, but a missing session needs
    # a mind to raise it and verify the panes. Read the latest scan's
    # session-stability sense; a fresh drift here turns Health RED so the
    # wake routes to a resolver instead of waiting on the operator window.
    try:
        snapshot = latest(home)
        stability = next((item for item in (snapshot.get("observations", []) if snapshot else [])
                          if isinstance(item, dict) and item.get("id") == "sense.host.session-stability"), None)
        if isinstance(stability, dict) and str(stability.get("state", "")) == "drift":
            causes.append("session-stability=" + _clip_sample(str(stability.get("sample", "drift")), 160))
    except (OSError, ValueError, AttributeError):
        pass
    # A churning session (repeated births = repeated deaths + re-raises) is a
    # host failure Health owns exactly like a missing session: the cadence
    # shape names the resolver's next probe. Same snapshot-re-read shape as
    # the stability block above; boring duplication over refactoring.
    try:
        snapshot = latest(home)
        restarts = next((item for item in (snapshot.get("observations", []) if snapshot else [])
                         if isinstance(item, dict) and item.get("id") == "sense.host.session-restarts"), None)
        if isinstance(restarts, dict) and str(restarts.get("state", "")) == "drift":
            causes.append("session-restarts=" + _clip_sample(str(restarts.get("sample", "drift")), 160))
    except (OSError, ValueError, AttributeError):
        pass
    # A server held by a unit that does not belong to the declared session —
    # a finished test's keeper, most often — is also a host failure Health
    # owns. The session-stability sense reads verified there (the session is
    # live and shielded), so without this cause an unowned keeper holding
    # the shared server would never route to a resolver before it is stopped.
    try:
        snapshot = latest(home)
        keeper = next((item for item in (snapshot.get("observations", []) if snapshot else [])
                       if isinstance(item, dict) and item.get("id") == "sense.host.server-keeper"), None)
        if isinstance(keeper, dict) and str(keeper.get("state", "")) == "drift":
            causes.append("server-keeper=" + _clip_sample(str(keeper.get("sample", "drift")), 160))
    except (OSError, ValueError, AttributeError):
        pass
    # Autonomy loop health: fog/landing/gate drift means minds cannot close
    # their own audit loops. Surface each as a named cause so the wake routes
    # to the owning resolver (genome for landing, witness for gates) instead
    # of stalling silently until the operator window intervenes.
    try:
        snapshot = latest(home)
        observations = snapshot.get("observations", []) if snapshot else []
        for sense_id, cause_prefix in (
                ("sense.coord.authorship-fog", "authorship-fog"),
                ("sense.repo.landing-block", "landing-block"),
                ("sense.coord.gate-latency", "gate-latency"),
                ("sense.mind.loop-stall", "loop-stall")):
            reading = next((item for item in observations
                            if isinstance(item, dict) and item.get("id") == sense_id), None)
            if isinstance(reading, dict) and str(reading.get("state", "")) == "drift":
                causes.append(cause_prefix + "=" + _clip_sample(str(reading.get("sample", "drift"))))
    except (OSError, ValueError, AttributeError):
        pass
    if linked_failed:
        causes.append("linked-sites=" + ",".join(linked_failed))
    if lease_state == "STALE":
        causes.append("pane-lease")
    # check that runs at most every 600s. Name the failing predicate and the
    # compute time so a latched RED is diagnosable after the transient is gone.
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if causes:
        verdict = f"FAIL health internal check at {at} — " + "; ".join(causes)
        lines.append("STATE: RED — internal check needs repair")
    elif bytes_state == "UNKNOWN" and (bytes_lines or bytes_error):
        # An unreadable renderer is broken monitoring, not a healthy pane; the
        # clause names it, so the headline a resolver reads first must not stay
        # silently GREEN while a renderer is unobserved.
        detail = _clip_sample(bytes_lines[0], 160) if bytes_lines else bytes_error
        verdict = f"UNKNOWN health renderer bytes {detail} at {at}"
        lines.append("STATE: UNKNOWN — a top-pane renderer is unreadable")
    elif redmi_unknown:
        verdict = f"UNKNOWN health Redmi10 monitor {redmi_unknown} at {at}"
        lines.append(f"STATE: UNKNOWN — Redmi10 monitor {redmi_unknown}")
    elif windows_unknown:
        verdict = f"UNKNOWN health {windows_unknown} at {at}"
        lines.append(f"STATE: UNKNOWN — {windows_unknown}")
    elif camera_unknown:
        # An unreadable camera source is broken monitoring, not a healthy pane:
        # stay UNKNOWN until the producer or the read is repaired.
        verdict = f"UNKNOWN health camera state unavailable at {at}"
        lines.append("STATE: UNKNOWN — camera state unavailable")
    elif pane_roots_unknown:
        # A pane pid whose environment cannot be read is unobserved, and a
        # stale pane root stays visible on its own line without an UNKNOWN.
        verdict = f"UNKNOWN health pane root data unavailable at {at}"
        lines.append("STATE: UNKNOWN — pane root data unavailable")
    elif linked_unknown:
        verdict = f"UNKNOWN health linked-site data unavailable at {at}"
        lines.append("STATE: UNKNOWN — linked-site service data unavailable")
    elif local_services_unknown:
        verdict = f"UNKNOWN health local service data unavailable at {at}"
        lines.append("STATE: UNKNOWN — local service data unavailable")
    elif not services:
        # `plant.write_service_manifest` writes an empty manifest for a site that
        # installed no services, so the pane's `_service_block` reads it as RED
        # ("an empty list is not healthy"). Name it in the durable report too, so
        # a latched pane RED is diagnosable here instead of reading PASS; a
        # malformed or missing manifest is handled above as UNKNOWN.
        verdict = f"FAIL health internal check at {at} — services-manifest-empty"
        lines.append("STATE: RED — internal check needs repair")
    elif not expected:
        verdict = f"UNKNOWN health expected windows unset at {at}"
        lines.append("STATE: UNKNOWN — expected windows unset")
    else:
        verdict = f"PASS health internal checks at {at}"
        lines.append("STATE: GREEN — internal checks pass")
    _report(home, "health", verdict)
    return "\n".join(lines) + "\n"


def permissions(home: Path) -> str:
    lines = ["GOAL: make every operator decision visible and scoped to the work it unblocks",
             "SELECT: bottom pane — Up/Down select, Enter grants one, q returns to shell",
             "COMMAND: permit (selector) | permit revoke (selector) | permit list | permit grant REQUEST_ID",
             "REQUEST: permit request ID --owner ROLE --task TASK --capability NAME --unblocks PATH --reason TEXT"]
    requests = list_requests(home)
    retired = retired_requests(home)
    pending = [(item, state) for item, state in requests if state == "pending" and item.identity not in retired]
    lines.append(f"PENDING: {len(pending)} · TOTAL: {len(requests)}")
    for item, state in requests[-20:]:
        display_state = f"RETIRED ({state.upper()})" if item.identity in retired else state.upper()
        lines.append(f"{item.identity} {display_state} owner={item.owner} task={item.task} "
                     f"capability={item.capability} unblocks={','.join(item.unblocks)}")
        if state == "pending" and item.identity not in retired:
            lines.append(f"  WHY: {item.reason}")
    active = recoveries(home)
    lines.append(f"RECOVERIES: {len(active)} unresolved obligations; keep other useful work moving")
    for row in active:
        status = "GRANTED; VERIFY RECOVERY" if row["status"] == "retry" else row["status"].upper()
        lines.extend([f"{row['id']} {status} owner={row['owner']} task={row['task']} resolver={row['resolver']}",
                      f"  MISSING: {row['missing']}", f"  NEXT: {row['action']}",
                      f"  ALTERNATIVES: {'; '.join(row['alternatives'])}",
                      f"  CUTOFF: {row['cutoff']}",
                      f"  EVIDENCE: {row['evidence']['path']}"])
    verdict = "UNKNOWN permission recovery route incomplete" if any(row["status"] == "unknown" for row in active) else "PASS permission requests visible"
    _report(home, "permissions", verdict)
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--view", choices=("discover", "senses", "health", "permissions"), required=True)
    parser.add_argument("--refresh-report", action="store_true",
                        help="health only: publish observations/health; prints REFRESHED or SKIPPED busy (producer mode)")
    args = parser.parse_args()
    if args.refresh_report:
        if args.view != "health":
            parser.error("--refresh-report applies to --view health only")
        print(refresh_health(args.home))
        return
    print(globals()[args.view](args.home), end="")


if __name__ == "__main__":
    main()
