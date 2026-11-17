"""Committed ordinary delivery, with functional probes and automatic recovery.

The mechanism and all scheduler/protocol changes require the strong path.
No legacy delivery_verified flag is minted here. A promotion affects only
future consumers, under the same lock held across a running wake.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from . import hold_text, page_paths_text, patch_text, release_boundary, units, work_boundary as boundary, wall_patch, proc_table
from .git_base_text import commit_intent_text
from .state_files import read_json, write_json, write_text_atomic
from .work_receipts import digest


# Everything outside the admitted leaf/docs list defaults to strong. The
# live list lives in work_boundary; this module only delegates to it.
def classification(paths):
    return boundary.release_classification(paths)


def advance_candidates(repo, pin, head=None):
    """Commits on the default branch after ``pin``, oldest first."""
    return boundary.commits_after(repo, pin, head)


def advance_target(repo, pin, *, commits=None, head=None):
    """The commit ``mishe release advance`` points the pin at: the newest commit after ``pin``.

    Delivery builds that commit fresh from git, runs the whole suite on it and observes the live
    consumers, so neither the remote CI result nor a path classification decides what may advance.
    Returns ``(sha, reason)``; ``sha`` is ``None`` with ``reason`` ``pin-is-head`` when nothing is newer.
    """
    commits = advance_candidates(repo, pin, head) if commits is None else list(commits)
    return (commits[-1], None) if commits else (None, "pin-is-head")


def current_link(home):
    """The one stable runtime path: every generated unit, the wrapper and plant units name this, never a sha."""
    return Path(home) / "runtime" / "current"


def point(home, source):
    """Atomically make runtime/current name ``source`` (a rename over the old link).

    Advancing and rolling back a release are this one operation, so nothing generated embeds a sha and
    nothing drifts: units are rewritten only when the unit set itself changes.
    """
    link = current_link(home)
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(Path(source).resolve(), tmp)
    os.replace(tmp, link)
    _mirror_pin_record(home, Path(source).resolve())


def _mirror_pin_record(home, source):
    """Write ``health/runtime-release.json`` from the pointer that was just moved.

    The pin sensor, pin readers and release readiness still read this record, so
    every pointer move (advance, rollback, revert) refreshes it. Nothing else
    writes it; a hand-edited record would drift from ``runtime/current``.
    """
    sha = boundary.head(source)
    path = Path(home) / "health" / "runtime-release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "source": str(source), "sha": sha,
                                     "session": "mishe-self-development-current"}) + "\n")
    temporary.replace(path)


def materialize(home, repo, sha):
    boundary.full_sha(repo, sha)
    source = Path(home) / "releases" / sha
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        boundary.git(repo, "worktree", "add", "--detach", str(source), sha)
    if boundary.head(source) != sha or not boundary.clean_tree(source):
        raise ValueError("release tree is not clean exact committed bytes")
    return source


def bind_reviewed_source(source, record):
    # Git preserves content and executable status, not checkout read/write bits.
    # Runtime replacement/recovery still binds complete snapshots separately.
    def git_snapshot(value):
        return None if value is None else (value["bytes"], bool(value["mode"] & 0o111))
    for name, values in record["files"].items():
        if git_snapshot(wall_patch.snapshot(Path(source)/name)) != git_snapshot(values["after"]):
            raise ValueError(f"committed release differs from reviewed bytes: {name}")


CHECK_TIMEOUT_S = 1500
"""The delivery check is the whole suite on the release tree (about 8 minutes)."""


def run(argv, *, cwd, env, timeout=900):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return dict(argv=argv, code=result.returncode, output=result.stdout + result.stderr, at=time.time())
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(argv=argv, code=None, output=str(exc), at=time.time())


def closure(source, env):
    check = source / "checks/promotion-import-closure.py"
    if not check.is_file(): raise ValueError("committed import closure checker missing")
    result = run([sys.executable, str(check), "--home", env["MISHE_SEED_HOME"], "--repo", str(source)], cwd=source, env=env)
    if result["code"] != 0: raise ValueError("committed import closure failed: " + result["output"])


@contextmanager
def delivery_lock(home, *, shared=False):
    """Exclusive by default; ``shared`` joins the readers instead of excluding them.

    A wake runner holds the same file *shared* for its whole run
    (``core.supervisor``: delivery waits for every running wake to end), so an
    exclusive taker is refused whenever any role is awake. A caller that only
    needs to exclude the exclusive writers -- delivery, activation, a review
    merge -- takes it shared and still runs during wakes. Two shared takers can
    overlap, so such a caller needs its own serialisation.
    """
    from .core.state import DispatchStore
    from .delivery_requests import check_admission
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    check_admission(home)
    path = DispatchStore(Path(home) / "dispatch").lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try: fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
        except OSError: raise DeliveryBusy("running wake or promotion holds dispatch lock; promote after wake settles") from None
        check_admission(home)
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DeliveryBusy(ValueError):
    """No effect: the existing dispatch exclusion is owned elsewhere."""


def _lock_holder_info(home, *, runner=subprocess.run):
    """Best-effort pid+argv of whoever holds the dispatch lock, for the give-up record.

    Diagnostic only: it never raises and never touches the lock. A missing ``lsof``/``ps``,
    a nonzero exit or an empty holder set is recorded as such instead of breaking the promotion.
    ``runner`` is injectable so the shape is testable without a real lock holder.
    """
    from .core.state import DispatchStore
    lock = str(DispatchStore(Path(home) / "dispatch").lock_path)
    try:
        pids_query = runner(["lsof", "-t", "--", lock], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(available=False, reason=f"lsof unavailable: {type(exc).__name__}")
    if pids_query.returncode != 0:
        return dict(available=False, reason=f"lsof exit {pids_query.returncode}")
    pids = proc_table.lsof_holder_pids(pids_query.stdout)
    if not pids:
        return dict(available=True, holders=[])
    argv_by_pid = {}
    try:
        detail = runner(["ps", "-o", "pid=,args=", "-p", ",".join(pids)],
                        capture_output=True, text=True, timeout=15, check=False)
        if detail.returncode == 0:
            argv_by_pid = proc_table.ps_argv_by_pid(detail.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(available=True, holders=[dict(pid=p, argv=None) for p in pids],
                    reason=f"ps unavailable: {type(exc).__name__}")
    return dict(available=True, holders=[dict(pid=p, argv=argv_by_pid.get(p)) for p in pids])


HOLD_TEXT = hold_text.PROMOTION_PREFIX


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _dispatch_lock_free(home):
    from .core.state import DispatchStore
    path = DispatchStore(Path(home) / "dispatch").lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def clear_stale_hold(home, *, pid_alive=_pid_alive, now=time.time, legacy_max_age_s=3600):
    """Remove a ``hold`` left behind by a promotion that died inside its activation window.

    Only promotion holds (``HOLD_TEXT`` prefix) are eligible. A hold written by ``held_delivery_lock`` carries the
    writer's pid (``pid=N``) and is stale when that pid is gone and the dispatch lock is free. A legacy promotion hold
    without a pid is stale only when older than ``legacy_max_age_s``. A manual ``mishe hold on`` hold carries no prefix
    and is never cleared here: it lasts until ``mishe hold off``. Returns True when the hold was removed. Never removes
    a hold whose writer is alive, or while a running wake or promotion holds the dispatch lock.
    """
    hold = Path(home) / "hold"
    try:
        text = hold.read_text()
        mtime = hold.stat().st_mtime
    except FileNotFoundError:
        return False
    if not hold_text.is_promotion_hold(text):
        return False  # an opaque manual hold is indefinite, irrespective of age or pid-looking text
    fields = hold_text.fields(text)
    if "pid" in fields and fields["pid"].isdigit():
        if pid_alive(int(fields["pid"])):
            return False
    elif now() - mtime < legacy_max_age_s:
        return False
    if not _dispatch_lock_free(home):
        return False
    try:
        if hold.read_text() != text:
            return False  # a new promotion rewrote it while we looked: leave the live hold alone
    except FileNotFoundError:
        return False
    hold.unlink(missing_ok=True)
    return True


@contextmanager
def held_delivery_lock(home, wait_s, *, sleep=time.sleep, patience_s=600, clock=time.monotonic, holder_probe=None):
    """The dispatch lock: first a quiet gap without holding anything, then (``wait_s`` > 0) under a short hold of new wakes.

    Wakes are short (median under a minute, p90 about 7 min) and at most a few run at once, so the lock is usually free
    for a moment. For ``patience_s`` seconds it is tried every second WITHOUT touching the hold, so a delivery that finds
    a gap costs no wake any delay. Only when no gap appears does the site's ``hold`` file stop new wakes, the running ones
    settle (the site hard cap bounds them), the lock is taken and the hold released (owed wakes resume) when the block
    ends, whatever happened inside it. Only a hold this call wrote is released: a manual ``mishe hold on``, plant_boot's
    boot-guard hold, or one rewritten while the delivery held, stays with its owner and fires no resume. ``wait_s`` 0
    takes the lock at once or raises DeliveryBusy.
    """
    if not wait_s:
        with delivery_lock(home):
            yield
        return
    stack = ExitStack()
    hold = Path(home) / "hold"
    held = False
    held_text = None      # the exact bytes this call wrote, so it removes only its own hold
    # Only a promotion hold with a dead writer is stale; a manual hold is not eligible.
    if clear_stale_hold(home):
        from . import wake
        from .roles import declared
        wake.resume_owed(home, declared(home), now=time.time())
    try:
        patience_until = clock() + patience_s
        while True:
            try:
                stack.enter_context(delivery_lock(home))
                break
            except DeliveryBusy:
                if not held and clock() >= patience_until:
                    # An existing hold already stops wake starts. Overwriting it would destroy
                    # a hold its owner still wants (a manual `mishe hold on`, or plant_boot's
                    # boot-guard hold that must outlive the boot), and unlinking it on the way
                    # out would resume wakes under that owner.
                    # Exclusive create, not exists()+write_text: a manual `mishe hold on`
                    # written in that window would otherwise be overwritten and unlinked
                    # on the way out. On FileExistsError the hold belongs to its owner.
                    if not hold.exists():
                        candidate = f"{HOLD_TEXT}\npid={os.getpid()} started={int(time.time())}\n"
                        try:
                            fd = os.open(hold, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        except FileExistsError:
                            pass
                        else:
                            with os.fdopen(fd, "w") as fh:
                                fh.write(candidate)
                            held_text = candidate
                    held = True
                    deadline = clock() + wait_s
                if held and clock() >= deadline:
                    exc = DeliveryBusy("running wake or promotion held the dispatch lock through the activation hold")
                    exc.holders = (holder_probe or _lock_holder_info)(home)
                    raise exc
                sleep(1 if not held else 5)
        yield
    finally:
        stack.close()
        if held_text is not None:
            try:
                still_ours = hold.read_text() == held_text
            except FileNotFoundError:
                still_ours = False
            if still_ours:
                hold.unlink(missing_ok=True)
                from . import wake
                from .roles import declared
                wake.resume_owed(home, declared(home), now=time.time())


def config_paths(home):
    unit_dir = Path.home() / ".config/systemd/user"
    from .release_boundary import owned_runtime_units
    # A shared filename prefix does not grant ownership of host or peer units.
    return [Path(home)/"bin/mishe", Path(home)/"bin/mishe-tauftauf", Path(home)/"coordination-mode.json", Path(home)/"units-installed",
            Path(home)/"resolvers.generated.json",
            *owned_runtime_units(home, unit_dir)]


def reload_units():
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=30)


DUAL_PROBE = "mishe-dual-probe.service"


def _probe_fields():
    out = subprocess.check_output(["systemctl", "--user", "show", DUAL_PROBE, "-p", "FragmentPath", "-p", "ActiveState"],
                                  text=True, timeout=30)
    return release_boundary.unit_fields(out)


def ensure_dual_probe(home, unit_dir=None):
    """The root-fs stall probe runs as the installed unit when one exists, otherwise as the transient it was before.

    systemd loads a transient unit (/run/user/...) ahead of an installed file of the same name, so while the
    transient runs the installed unit is never the fragment. Stop the transient, reload, then enable and start
    the installed unit. Without an installed unit (revert), start the transient again so the probe never lapses.
    """
    unit_dir = Path(unit_dir) if unit_dir else Path.home() / ".config/systemd/user"
    durable = unit_dir / DUAL_PROBE
    if durable.is_file():
        fields = _probe_fields()
        if fields.get("FragmentPath") != str(durable):
            if not release_boundary.is_transient_fragment(fields.get("FragmentPath", "")):
                raise RuntimeError(f"root-fs probe fragment UNKNOWN: {fields}")
            subprocess.run(["systemctl", "--user", "stop", DUAL_PROBE], check=True, capture_output=True, timeout=60)
            reload_units()
        subprocess.run(["systemctl", "--user", "enable", DUAL_PROBE], check=True, capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "start", DUAL_PROBE], check=True, capture_output=True, timeout=60)
        fields = _probe_fields()
        if fields.get("FragmentPath") != str(durable) or fields.get("ActiveState") != "active":
            raise RuntimeError(f"root-fs probe not running from its installed unit: {fields}")
        return "installed"
    if _probe_fields().get("ActiveState") != "active":
        script = Path(home) / "operator" / "dual_probe.py"
        subprocess.run(["systemd-run", "--user", "--collect", f"--unit={DUAL_PROBE}", "--slice=mishe-plants.slice",
                        "--property=Restart=always", "--property=RestartSec=60",
                        f"--working-directory={Path(home) / 'operator'}", "--setenv=MISHE_WORKLOAD_SLICE=mishe-plants.slice",
                        "/usr/bin/python3", str(script)], check=True, capture_output=True, timeout=60)
    return "transient"


PROBE_STALE_S = 300


def probe_problems(home, unit_dir=None, *, now=None, fields=None):
    """Doctor reasons the root-fs probe is not live from its installed unit with fresh samples; [] when healthy."""
    unit_dir = Path(unit_dir) if unit_dir else Path.home() / ".config/systemd/user"
    durable = unit_dir / DUAL_PROBE
    problems = []
    if not durable.is_file():
        problems.append(f"root-fs probe: installed unit {durable} missing")
    try:
        fields = _probe_fields() if fields is None else fields
    except (OSError, subprocess.SubprocessError) as exc:
        return problems + [f"root-fs probe: systemctl unreadable ({type(exc).__name__})"]
    if fields.get("ActiveState") != "active":
        problems.append(f"root-fs probe: {DUAL_PROBE} ActiveState={fields.get('ActiveState', 'UNKNOWN')}")
    if durable.is_file() and fields.get("FragmentPath") != str(durable):
        problems.append(f"root-fs probe: loaded fragment {fields.get('FragmentPath', 'UNKNOWN')!r} is not the installed unit")
    now = time.time() if now is None else now
    newest = release_boundary.newest_sample_at(Path(home) / "operator" / "dual-probe.jsonl")
    if newest is None:
        problems.append("root-fs probe: no readable sample in operator/dual-probe.jsonl")
    elif now - newest > PROBE_STALE_S:
        problems.append(f"root-fs probe: newest sample {now - newest:.0f}s old (limit {PROBE_STALE_S}s)")
    return problems


def restore_configuration(home, before):
    """Remove newly created triggers before restoring the prior exact files."""
    from .release_boundary import trigger_unit
    operator_wrappers = {str(Path(home)/"bin"/name) for name in ("mishe", "mishe-tauftauf")}
    for path in config_paths(home):
        if str(path) not in before:
            # A new installed root-fs probe is stopped and disabled before its file goes; ensure_dual_probe restarts the transient.
            if trigger_unit(path) or path.name == DUAL_PROBE:
                subprocess.run(["systemctl", "--user", "disable", "--now", path.name],
                               check=True, capture_output=True, timeout=30)
            # A wrapper names the stable runtime/current path, not the pin, so it stays valid
            # after revert. A snapshot taken before the wrapper was tracked does not list it;
            # deleting it then broke the operator CLI (0d210604 revert, 23:45Z).
            if str(path) not in operator_wrappers:
                wall_patch.restore(path, None)
    for name, value in before.items():
        wall_patch.restore(Path(name), value)
    reload_units()
    ensure_dual_probe(home)


def activate(home, repo, source, sha):
    from .runtime_cli import run_units
    import argparse
    # Preserve existing trigger state; a newly declared owned trigger must
    # actually start. Recovery removes it and stops it if activation fails.
    previous_paths = set(config_paths(home))
    marker = wall_patch.snapshot(Path(home) / "units-installed")
    point(home, source)                      # consumers resolve the stable path; the pin is the pointer
    stable = current_link(home)
    # no_enable=True: the sync installs unit files and reloads systemd but enables NO existing trigger, so a
    # trigger that was disabled before activation stays disabled here and in revert. Only triggers newly
    # declared by this release are enabled below, and restore_configuration disables exactly those.
    args = argparse.Namespace(command="sync", repo=repo, source_repo=source, pythonpath=str(stable/"src"),
        unit_dir=Path.home()/".config/systemd/user", prefix="mishe", no_slices=False,
        no_systemctl=False, no_enable=True, uninstall=False)
    run_units(args, Path(home), time.time())
    from .release_boundary import trigger_unit
    for path in config_paths(home):
        if path not in previous_paths and trigger_unit(path):
            subprocess.run(["systemctl", "--user", "enable", "--now", path.name],
                           check=True, capture_output=True, timeout=30)
    ensure_dual_probe(home)                  # the root-fs probe must run from its installed unit before consumers are observed
    wall_patch.restore(Path(home)/"units-installed", marker)
    # No shell interpolation of paths: shlex is used only for the installed POSIX wrapper. Both operator wrapper names name the stable
    # path (the doctor checks bin/mishe-tauftauf; a stale pin there kept it RED after every earlier delivery).
    from .release_boundary import wrapper_text
    for name in ("mishe", "mishe-tauftauf"):
        wrapper = Path(home)/"bin"/name
        wrapper.write_text(wrapper_text(stable, sys.executable, home=home)); wrapper.chmod(0o755)
    config = read_json(Path(home)/"coordination-mode.json") or {}
    config["runtime"] = str(source)
    write_json(Path(home)/"coordination-mode.json", config)
    # Something else (a sync by another mind, a trigger enabled above) can touch a unit file after the sync's own reload:
    # end the activation with one more reload so the loaded configuration matches the files the observation will compare.
    reload_units()


OUTCOME_SIGNAL = "moving-pin-delivery"


def outcome_detail(record):
    """The one line a subscriber reads when the promotion did not deliver, or "" when it did.

    The woken reviewer on ``moving-pin-delivery`` gets no check of its own: the signal carries the
    bare token FAILED unless the failing sha, phase and diagnostic are written beside it. This is
    the ``signals/NAME.detail`` a producer writes on a non-GREEN run, and reads back the same way —
    ``checks/pin-delivery.py`` chooses for genome's ``pin-delivery`` signal, mirrored here so the two
    signals name the same reason for the same failure.
    """
    sha = str(record.get("sha", ""))[:8]
    phase = record.get("phase")
    if phase == "delivered":
        return ""
    reason = (record.get("failure")
              or release_boundary.failing_line((record.get("check") or {}).get("output"))
              or record.get("rollback_error") or "no reason recorded")
    return f"{OUTCOME_SIGNAL}: {sha} {phase}: {reason}"


def publish_outcome(home, record):
    """Publish the promotion outcome as the ``moving-pin-delivery`` signal: VERIFIED when delivered, FAILED otherwise.

    The sha is the event, so a second delivery with the same reading is still an edge and wakes its subscribers
    (field-review reviews every pin move). A missing registration or write error is recorded on the record, never raised.
    """
    from . import signal_store
    phase = record.get("phase")
    value = "VERIFIED" if phase == "delivered" else "FAILED"
    try:
        signal_store.observe(home, OUTCOME_SIGNAL, value, now=time.time(), source="release_promotion",
                             event=f"{record.get('sha', '')}:{phase}")
        # Beside the reading, the reason a reviewer reads: the failing sha, phase and diagnostic.
        # A delivered promotion removes any stale failure so it is not shown against a fresh VERIFIED.
        detail = Path(home) / "signals" / f"{OUTCOME_SIGNAL}.detail"
        line = outcome_detail(record)
        if line:
            write_text_atomic(detail, line + "\n")
        else:
            detail.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        record["signal_error"] = f"{type(exc).__name__}: {exc}"
    return record


def _save_signal_error(home, record, *, sleep=time.sleep, attempts=40, delay=0.05):
    """Persist ``publish_outcome``'s ``signal_error`` without overwriting a merged review.

    ``_promote`` saved the complete record while it held the delivery lock; the only
    new field here is the one ``publish_outcome`` writes when the signal store fails.
    A review poll can merge its verdict between the two writes, so this updates the
    record found on disk under ``review_records_lock`` rather than rewriting it from
    this (possibly stale) copy.
    """
    path = Path(home) / "work/releases" / f"{record['id']}.json"
    for attempt in range(attempts):
        try:
            with review_records_lock(home):
                current = read_json(path) or dict(record)
                current["signal_error"] = record["signal_error"]
                write_json(path, current)
            return
        except DeliveryBusy:
            if attempt + 1 >= attempts:
                # The delivery itself is complete and the failure line is also in the
                # signal detail; a wedged scan must not turn this into a failed promotion.
                return
            sleep(delay)


def promote(home, repo, sha, **kwargs):
    """Promote ``sha`` as the one pin (see ``_promote``) and publish the outcome signal."""
    record = _promote(home, repo, sha, **kwargs)
    publish_outcome(Path(home), record)
    if "signal_error" in record:
        _save_signal_error(Path(home), record)
    return record


def enqueue_review(home, record):
    root = Path(home)/"work/reviews"; root.mkdir(parents=True, exist_ok=True)
    path = review_queue_path(home, record)
    if path.exists():
        queue = read_json(path)
        identities = queue.get("release_ids", [queue["release_id"]])
        if record["id"] not in identities:
            identities.append(record["id"])
        write_json(path, dict(queue, release_ids=identities))
        if "review" in queue.get("result", {}):
            write_json(root/f"{record['id']}.result.json", dict(id=record["id"], result=queue["result"], merged=False))
        return
    write_json(path, dict(sha=record["sha"], release_id=record["id"], release_ids=[record["id"]], state="pending"))
    unit_key = record["sha"][:12] + ("-" + record["previous"][:12] if record.get("previous") else "")
    subprocess.run(["systemd-run", "--user", "--collect", f"--unit=mishe-release-review-{unit_key}",
                    "--slice=mishe-plants.slice",
                    "--setenv=PYTHONPATH=" + str(Path(record["source"])/"src"),
                    "--setenv=MISHE_SEED_HOME=" + str(home),
                    sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "release", "review", "--id", record["id"]],
                   env=dict(os.environ, PYTHONPATH=str(Path(record["source"])/"src")), capture_output=True, timeout=15, check=True)


REVIEW_RETRY_BASE_S = 1800
"""First delay before a failed asynchronous review is re-run."""

REVIEW_RETRY_MAX_ATTEMPTS = 8
"""How many failed reviews a release re-runs before it stops trying."""

REVIEW_EXHAUSTED_DUE_S = 21600
"""How long an owner has to answer a spent review-retry ladder before the intention returns."""

REVIEW_RETRY_REARM_GRACE_S = 900
"""How long past its due time a recorded retry may stay silent before it is armed again.

The retry is a transient ``systemd-run --on-calendar`` unit, and a transient timer
does not survive a stop/start cycle: the root-migration flip's quiesce stops every
unit that names a moved tree and ``--collect`` removes the stopped unit, so the
restore cannot start that name again (``restart failed:
mishe-release-review-retry-...``, 2026-11-17). The attempt count cannot see the
loss -- the retry ladder only advances when a retry actually *runs* and archives a
failed attempt -- so the record would keep ``review_pending`` true and the armed
state forever. A record whose due time is this far past is therefore treated as
lost and armed again; the grace keeps a review that is merely running late from
being duplicated.
"""


def _review_attempts(home, identity):
    """Number of failed reviews archived for one release, the retry ladder's rung."""
    return len(list((Path(home)/"work/reviews").glob(f"{identity}-*.failed-attempt.json")))


def _review_retry_lost(current, now, live_units=frozenset(), timer_shas=frozenset()):
    """True when a recorded retry rung must be armed again.

    A transient unit cannot be started again once a stop/collect cycle removed
    it, and it leaves no result, so the record itself has to notice the loss. A
    rung is lost when

    * its unit is not among ``live_units`` and a recorded ``review_retry_due`` is
      more than ``REVIEW_RETRY_REARM_GRACE_S`` past -- the unit should have fired
      by now; the grace keeps a review that is merely running late from being
      duplicated, or
    * it is live only as a ``.timer`` (its sha is in ``timer_shas``) and that due
      time is this far past: a timer whose deadline came and went without firing
      was postponed, not merely late. A user-manager ``daemon-reload`` gave every
      pending ``--on-active`` retry a new base and pushed sixteen of them to
      reload+delay (2026-11-17), so the deadline the record names is the truth.
      A live ``.service`` is a review running now and is never duplicated, or
    * the record is legacy, naming only ``scheduled in ...`` from before the due
      time was recorded (2026-11-17). Its unit may still exist; ``live_units``
      answers that exactly, where the record cannot, or
    * the record names no retry at all but already carries a ``review_error``:
      the review finished with an error before the ladder existed, and a merged
      record is never revisited, so it is as lost as a destroyed unit.

    A record with neither a due time, a recorded retry state nor a
    ``review_error`` was never armed: its first review is still running and
    nothing is lost.
    """
    sha = (current.get("sha") or "")[:12]
    due = current.get("review_retry_due")
    overdue = isinstance(due, (int, float)) and now >= due + REVIEW_RETRY_REARM_GRACE_S
    if sha in live_units:
        return overdue and sha in timer_shas
    if isinstance(due, (int, float)):
        return overdue
    # Legacy records (2026-11-17, before the due time was recorded) carry only
    # the state line. Key presence separates an armed record from one never
    # armed; "exhausted" is this module's own value for a spent ladder and is
    # written after a scheduling attempt, never before one.
    if "review_retry_state" in current:
        return current["review_retry_state"] != "exhausted"
    # Nothing was ever armed. A record whose review already reported an error is
    # not a first review still running: its result is merged and no other path
    # revisits a merged record, so it would keep review_pending true for good
    # (2026-11-17: two delivered records with merged: true, a reviewer error,
    # no attempts and no retry state). A fresh record has no review_error and is
    # still left alone.
    return "review_error" in current


def review_retry_delay(home, identity, base=REVIEW_RETRY_BASE_S, attempts_max=REVIEW_RETRY_MAX_ATTEMPTS):
    """Seconds to wait before re-running a failed review, or None when spent.

    A reviewer outage used to strand a release's asynchronous review for good:
    ``enqueue_review`` starts exactly one unit and nothing invoked it again
    (2026-11-17: the Claude account's weekly quota was spent, the reviewer exited
    non-zero and the record kept ``review_pending`` true with no retry). Every
    failed attempt already leaves a ``<identity>-<ns>.failed-attempt.json``
    archive, so the number of archives counts the attempts without new state.
    The delay doubles up to eight times the base — roughly 30m, 1h, 2h, 4h then
    8h each — which spans about forty hours of outage before giving up.
    """
    done = _review_attempts(home, identity)
    if done >= attempts_max:
        return None
    return base * (2 ** min(done, 4))


def _calendar_time(due):
    """The ``systemd-run --on-calendar`` argument for an epoch deadline."""
    return datetime.fromtimestamp(due, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def schedule_review_retry(home, record, *, due, runner=None):
    """Start one unit that re-runs this release's failed review at ``due`` (epoch seconds).

    The timer names an absolute calendar time, not ``--on-active``: a
    user-manager ``daemon-reload`` — every activation does one — recomputes a
    monotonic timer's base from the reload, so a pending retry is pushed back
    to reload+delay and can be starved for good while reloads keep arriving.
    Measured 2026-11-17: sixteen retries due 16:05–16:32 all moved to
    16:26:47+delay after one reload, and none had fired. A calendar deadline
    keeps its time across reloads (same measurement).

    Returns the deadline the timer was armed for: at least one second ahead,
    because a calendar time already past never fires.
    """
    run = subprocess.run if runner is None else runner
    at = max(int(due), int(time.time()) + 1)
    unit = f"mishe-release-review-retry-{record['sha'][:12]}-{at}"
    pythonpath = str(Path(record["source"])/"src")
    run(["systemd-run", "--user", "--collect", f"--unit={unit}",
         f"--on-calendar={_calendar_time(at)}",
         "--slice=mishe-plants.slice",
         "--setenv=PYTHONPATH=" + pythonpath,
         "--setenv=MISHE_SEED_HOME=" + str(home),
         sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
         "release", "review", "--id", record["id"]],
        env=dict(os.environ, PYTHONPATH=pythonpath),
        capture_output=True, timeout=15, check=True)
    return at


def _announce_exhausted_review_retry(home, current):
    """Make a spent review-retry ladder an obligation instead of silence.

    ``_review_retry_lost`` answers False for "exhausted", so no later scan arms
    the record again and its next failed attempt never merges: before this, eight
    failed reviews ended as a record that kept ``review_pending`` true with no due
    time, no intention and no message (2026-11-17). Every other non-clear outcome
    of ``merge_review`` raises an intention; the terminal one did not. The
    intention id is keyed on the sha, so a repeated call overwrites rather than
    accumulating obligations.
    """
    from . import intentions
    from .failure import default_notify
    owner = current.get("owner")
    if not isinstance(owner, str) or not owner:
        owner = "genome"                    # the attribution fallback of a delivered record
    path = record_path(home, current["id"])
    try:
        intentions.add(home, owner, "review-exhausted-" + (current.get("sha") or "")[:12],
                       note=(f"Independent review of {current['id']} is still pending after "
                             f"{REVIEW_RETRY_MAX_ATTEMPTS} failed retries; the ladder is spent and no unit "
                             f"is armed. Re-run the review by hand or accept the release without one. "
                             f"Record: {path}"),
                       due=time.time() + REVIEW_EXHAUSTED_DUE_S)
        default_notify(home, owner,
                       f"Release {current['id']}: independent review still unavailable after "
                       f"{REVIEW_RETRY_MAX_ATTEMPTS} retries; review_pending stays true and no further retry "
                       f"is armed. Record: {path}")
    except (OSError, RuntimeError, ValueError) as exc:
        # A broken intentions directory or uninstalled chat tape must not abort
        # the exhaustion write: record why the owner was not told instead.
        current["review_retry_notice_error"] = str(exc)


def schedule_review_retry_if_needed(home, current):
    """Record one delayed re-run for ``current``'s newest unanswered review error.

    Both merge paths reach a review error: ``review()`` merging its own result and
    the work-watch poll merging a result a wake deferred behind the dispatch lock.
    Before this only ``review()`` scheduled the retry, so a deferred merge recorded
    ``review_error`` and left ``review_pending`` true with no retry unit
    (2026-11-17), stranding the release exactly as the in-process path used to.

    The retry is recorded once per failed attempt, keyed on the number of
    failed-attempt archives already written: a wake that merges the same result
    twice (or reviews and the poll racing) schedules only one unit, while the
    next failed attempt raises the count and schedules the next rung of
    ``review_retry_delay``. Gating on ``review_retry_state`` alone latched after
    the first retry and left the ladder's eight attempts unreachable
    (2026-11-17). The caller holds the delivery lock and writes ``current``
    afterwards.

    The scheduled time is recorded as ``review_retry_due`` so a retry unit that a
    stop/collect cycle removed is armed again once that time is
    ``REVIEW_RETRY_REARM_GRACE_S`` past, instead of stranding the review for good.

    Returns True only when a unit was armed by this call. A caller that stops a
    superseded timer must require it: when the ladder is spent or the start
    failed, the old unit is the only pending attempt and stopping it loses the
    last review.
    """
    if current.get("review_pending") is False:
        return False
    attempts = _review_attempts(home, current["id"])
    if current.get("review_retry_attempts") == attempts:
        # The recorded rung was already scheduled. Arm it again only when the
        # unit is gone: due long past with no archive since, or a legacy record
        # whose due time was never recorded. Both are a retry a stop/collect
        # cycle removed (a flip, a unit reset), not one waiting to fire.
        if not _review_retry_lost(current, time.time()):
            return False
    delay = review_retry_delay(home, current["id"])
    try:
        if delay is None:
            current["review_retry_state"] = "exhausted"
            current.pop("review_retry_due", None)
            _announce_exhausted_review_retry(home, current)
        else:
            armed = schedule_review_retry(home, current, due=time.time() + delay)
            current["review_retry_state"] = f"scheduled in {int(delay)}s"
            current["review_retry_due"] = armed
    except (OSError, subprocess.SubprocessError) as exc:
        # Leave the attempt uncounted so a later merge retries the scheduling.
        current["review_retry_state"] = f"scheduling failed: {exc}"
        return False
    current["review_retry_attempts"] = attempts
    return delay is not None


def _identity(record):
    """The review identity a record names, or ``None`` when it names none.

    A partially written or malformed record must not fail the whole work poll
    (review note on 3a2379dd, 2026-11-17): this scan can only act on a record
    that names its identity, so it skips the rest and leaves their repair to
    whoever owns that record.
    """
    identity = record.get("id")
    return identity if isinstance(identity, str) and identity else None


def _postponed_retry_timers(record, live):
    """The live ``.timer`` units for ``record``'s sha."""
    return list(live.get((record.get("sha") or "")[:12], {}).get("timers", ()))


@contextmanager
def review_records_lock(home):
    """Serialise the read-modify-write of ``work/releases`` review records.

    The re-arm scan and ``merge_review`` each read a record, decide, and write it
    back; a delivery or activation writes records too but takes ``delivery_lock``
    exclusively, which both callers still take *shared*. This lock orders the two
    shared readers against each other, which ``delivery_lock`` cannot do: it is
    held shared by every running wake, so making either caller exclusive there
    starves it exactly while any role is awake (measured 2026-11-17: all sixteen
    overdue retries and seventy queued review results were refused).

    Non-blocking: a busy record write is this poll's skip and the next poll's
    turn. The scan holds it for one pass and a merge for one result.
    """
    path = Path(home)/"work"/".review-records.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DeliveryBusy("another scan or merge holds the review records") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def rearm_lost_review_retries(home, *, clock=time.time, live_units=None, stop=None, skipped=None):
    """Re-arm the retries whose transient unit a stop/collect cycle removed or postponed.

    ``merge_review`` only reaches a record when a result arrives, and a destroyed
    retry unit never produces one, so the loss needs a scan: ``work poll`` runs
    every couple of minutes over the whole home. ``_review_retry_lost`` decides
    which records are affected; a record whose retry unit is still live and whose
    deadline has not been missed is left untouched, as is one running a review
    now. The scan takes ``delivery_lock`` *shared*, so it runs while a wake holds
    the same lock shared and is still refused while a delivery or activation holds
    it exclusively (measured 2026-11-17: an exclusive scan was refused exactly
    while any role was awake, so all sixteen overdue records were skipped and the
    re-arm never happened). ``review_records_lock`` serialises the scan against
    ``merge_review``, which now shares the same two locks; two polls cannot arm
    the same rung twice, and ``skipped`` collects the identities a delivery or
    activation held off so the poll line shows the starvation. A
    live timer whose recorded deadline is long past is replaced: the calendar
    rung is armed first and the postponed timer is then stopped, so only one rung
    is pending.

    ``live_units`` is the ``units.live_review_retry_units()`` answer; it is
    computed once per scan so the whole home costs one manager query and every
    record sees the same snapshot.

    Returns the identities re-armed, for the poll's log line.
    """
    try:
        with review_records_lock(home):
            return _rearm_scan(home, clock=clock, live_units=live_units, stop=stop, skipped=skipped)
    except DeliveryBusy:
        # A merge is writing a result; this poll leaves the record to the next one.
        return []


def _rearm_scan(home, *, clock, live_units, stop, skipped):
    """The single scanned pass: called only while ``review_records_lock`` is held."""
    live = units.live_review_retry_units() if live_units is None else live_units
    live_shas = frozenset(live)
    timer_shas = frozenset(sha for sha, groups in live.items() if groups.get("timers"))
    stop = _stop_timers if stop is None else stop
    armed = []
    for path in sorted((Path(home)/"work/releases").glob("*.json")):
        try:
            current = read_json(path)
            if not current or current.get("review_pending") is not True:
                continue
            identity = _identity(current)
            if identity is None:
                continue
            if (current.get("review_retry_attempts") or 0) != _review_attempts(home, identity):
                continue
            if not _review_retry_lost(current, clock(), live_shas, timer_shas):
                continue
            try:
                with delivery_lock(home, shared=True):
                    current = read_json(path)
                    if not current or current.get("review_pending") is not True:
                        continue
                    identity = _identity(current)
                    if identity is None:
                        continue
                    if (current.get("review_retry_attempts") or 0) != _review_attempts(home, identity):
                        continue
                    if not _review_retry_lost(current, clock(), live_shas, timer_shas):
                        continue
                    if schedule_review_retry_if_needed(home, current):
                        # The replacement is armed; the postponed timer must not fire
                        # a second review. When the ladder is spent or the start
                        # failed nothing replaced it, and the old unit is left to run
                        # its one attempted review rather than be lost to a
                        # scheduling decision.
                        stop(_postponed_retry_timers(current, live))
                    write_json(path, current)
            except DeliveryBusy:
                # A delivery or activation holds the lock exclusively; this poll
                # cannot read-modify-write safely. Count it so the skip is visible
                # instead of looking like nothing was overdue.
                if skipped is not None:
                    skipped.append(identity)
                continue
        except (OSError, ValueError, subprocess.SubprocessError):
            # A transient start failure or a record being rewritten keeps its
            # evidence; the next poll re-reads it. A malformed record is not
            # this scan's to repair, and must not fail the whole poll.
            continue
        armed.append(identity)
    return armed


def _stop_timers(names):
    """Stop the named transient timers; already-elapsed ones make this a no-op."""
    if names:
        subprocess.run(["systemctl", "--user", "stop", *names],
                       capture_output=True, text=True, timeout=30)


def review_queue_path(home, record):
    # The same SHA from a different base has a different scoped diff to read.
    key = record["sha"] + ("-" + record["previous"] if record.get("previous") else "")
    return Path(home)/"work/reviews"/f"{key}.json"


def _promote(home, repo, sha, *, check, observe, previous, activate=None, probe=run,
            reviewer=None, closure=closure, strong_patch=None, request_id=None, wait_s=0):
    home, repo = Path(home), Path(repo)
    boundary.full_sha(repo, sha); boundary.full_sha(repo, previous)
    paths = boundary.changed_paths(repo, previous, sha)
    kind = classification(paths)
    if kind == "strong":
        if not strong_patch: raise ValueError("high-impact change requires strong reviewed recovery evidence")
        strong = wall_patch.status(home, strong_patch)
        if not wall_patch.verified_delivery(strong): raise ValueError("strong patch has not passed recovery exercise")
        for path in paths:
            if path not in strong["files"]: raise ValueError(f"strong review does not cover {path}")
    with ExitStack() as stack:
        if activate is None:
            from .runtime_cli import default_pythonpath
            if Path(default_pythonpath()).resolve() != (home/"releases"/previous/"src").resolve():
                raise ValueError("previous SHA does not match installed wake consumer pin")
        source = materialize(home, repo, sha)
        if kind == "strong":
            bind_reviewed_source(source, strong)
        identity = sha + "-" + str(time.time_ns())
        record = dict(version=1, id=identity, sha=sha, previous=previous, source=str(source), paths=paths,
                      request_id=request_id,
                      created_at=time.time(),
                      classification=kind, phase="checking", tests_passed=False, activated=False, observed=False,
                      rollback_available=False, review_pending=kind=="ordinary", events=[])
        path = home/"work/releases"/f"{identity}.json"
        def save(): write_json(path, record)
        save()
        env = dict(os.environ, PYTHONPATH=str(source/"src"), MISHE_SEED_HOME=str(home))
        # check and observe run with cwd=source, so relative argv binds to the release tree.
        commands = wall_patch.command_artifacts(home, [check, observe], cwd=source)
        record["command_artifacts"] = commands
        record["observe_argv"] = observe
        from .roles import declared
        attributed_owner = boundary.commit_role(repo, sha)
        record["owner"] = attributed_owner if attributed_owner in declared(home) else "genome"
        record["check"] = run(check, cwd=source, env=env, timeout=CHECK_TIMEOUT_S)
        if record["check"]["code"] != 0:
            record["phase"]="check-failed"; save(); return record
        try:
            closure(source, env)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # The committed import closure is part of the check stage; a release whose tree cannot
            # import must publish FAILED like any other check failure, not stay "checking".
            record["phase"]="check-failed"; record["failure"]=str(exc)
            publish_outcome(home, record); save(); raise
        if not boundary.clean_tree(source) or boundary.head(source) != sha:
            record["phase"]="check-mutated-source"; record["failure"]="committed source changed during checks"
            publish_outcome(home, record); save(); raise ValueError(record["failure"])
        if commands != wall_patch.command_artifacts(home, [check, observe], cwd=source):
            record["phase"]="check-mutated-command"; record["failure"]="command changed during check"
            publish_outcome(home, record); save(); raise ValueError(record["failure"])
        record["tests_passed"]=True
        # Only now the dispatch lock: the check above ran the whole suite without holding wakes back.
        record["phase"]="waiting-for-wakes"; save()
        try:
            stack.enter_context(held_delivery_lock(home, wait_s))
        except DeliveryBusy as exc:
            record["held_delivery_lock_holder"] = getattr(exc, "holders", None)
            record["phase"] = "delivery-busy"
            record["failure"] = (f"dispatch lock still held after a {int(wait_s)}s activation hold"
                                 if wait_s else "dispatch lock busy")
            publish_outcome(home, record); save()
            raise
        record["phase"]="activating"; save()
        configs = {str(p):wall_patch.snapshot(p) for p in config_paths(home)} if activate is None else {}
        record["config_before"]=configs; record["rollback_available"]=True; save()
        switch = activate or (lambda root, target: globals()["activate"](home,repo,root,target))
        try:
            record["activation_started_at"] = time.time(); save()
            switch(source,sha); record["activated"]=True; record["events"].append("activated"); save()
            # the probe drains consumer invocations that started before this instant (pin_observation)
            env = dict(env, MISHE_PIN_CUTOFF_US=str(time.monotonic_ns() // 1000))
            if activate is None:
                record["consumer_invocation"] = boundary.invoke_arm_consumer(source/"src")
            record["observe"] = probe(observe,cwd=source,env=env)
            if record["observe"]["code"] != 0:
                # The probe's own line is the cause a subscriber needs (2026-11-17: the detail read
                # only "functional probe failed" while the record held the unit that vanished).
                diagnostic = release_boundary.failing_line(record["observe"].get("output"))
                raise ValueError("functional probe failed" + (f": {diagnostic}" if diagnostic else ""))
            record["config_after"] = {str(p):wall_patch.snapshot(p) for p in config_paths(home)} if activate is None else {}
            record.update(observed=True,phase="delivered",delivered_at=time.time()); save()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record["failure"] = str(exc); record["events"].append("rollback-started"); save()
            record["rollback_at"] = time.time()
            try:
                old = materialize(home,repo,previous)
                if activate is not None: switch(old,previous)
                else:
                    restore_configuration(home, configs)
                    point(home, old)
                    record["restored_consumer_invocation"] = boundary.invoke_arm_consumer(old/"src")
                # the probe code is the NEW source (an older release may not carry it); cwd names the release being observed
                record["rollback_probe"] = probe(observe,cwd=old,env=dict(env,MISHE_PIN_CUTOFF_US=str(time.monotonic_ns() // 1000)))
                record["phase"]="rolled-back" if record["rollback_probe"]["code"]==0 else "UNKNOWN"
                record["rollback_completed_at" if record["phase"] == "rolled-back" else "rollback_failed_at"] = time.time()
            except (OSError,ValueError,subprocess.SubprocessError) as restore_error:
                record.update(phase="UNKNOWN",rollback_error=str(restore_error),rollback_failed_at=time.time())
            save(); return record
        if kind == "ordinary":
            try: (reviewer or (lambda r:enqueue_review(home,r)))(record)
            except (OSError,ValueError,subprocess.SubprocessError) as exc: record["review_error"]=str(exc)
            save()
        return record



def record_path(home, identity):
    from .observations import validate_slug
    validate_slug(identity)
    return Path(home)/"work/releases"/f"{identity}.json"


def revert(home, identity):
    path = record_path(home, identity)
    with delivery_lock(home):
        record = read_json(path)
        if not record or record["phase"] != "delivered": raise ValueError("revert needs a delivered release")
        if {str(p) for p in config_paths(home)} != set(record.get("config_after", {})):
            raise ValueError("consumer configuration inventory changed since promotion; reconcile later owned additions before revert")
        for name, value in record.get("config_after", {}).items():
            if wall_patch.snapshot(Path(name)) != value: raise ValueError("consumer configuration changed since promotion")
        record.update(phase="reverting", revert_started_at=time.time())
        write_json(path, record)
        try:
            restore_configuration(home, record["config_before"])
            old = Path(home)/"releases"/record["previous"]
            point(home, old)
            record["restored_consumer_invocation"] = boundary.invoke_arm_consumer(old/"src")
            record["revert_probe"] = run(record["observe_argv"], cwd=old,
                env=dict(os.environ, PYTHONPATH=str(old/"src"), MISHE_SEED_HOME=str(home)))
            record["phase"] = "reverted" if record["revert_probe"]["code"] == 0 else "UNKNOWN"
            record["reverted_at" if record["phase"] == "reverted" else "revert_failed_at"] = time.time()
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            record.update(phase="UNKNOWN",revert_error=str(exc),revert_failed_at=time.time())
        write_json(path,record)
        return record


def _release_intent(source, previous, sha):
    """The release window's commit messages, minus attribution trailers.

    The messages are the author's own statement of what changed and why; a
    reviewer reading only the diff cannot always recover that intent. A missing
    or broken worktree yields "" rather than failing the review.
    """
    try:
        raw = boundary.git(source, "log", "--format=%h %s%n%b", f"{previous}..{sha}")
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        # AttributeError covers tests that stub the shared subprocess module; this
        # helper must never fail a review, so any git trouble yields no intent.
        return ""
    return commit_intent_text(raw)


DESCRIBED_PATH_MAX = 12
"""How many repo files one release may add to a review context this way."""

_DESCRIBED_MAX_BYTES = 256_000
_DESCRIBED_TOTAL_BYTES = 320_000
"""Total bounded addition: half ``wall_patch``'s own ``CONTEXT_TOTAL_BYTES``."""


def _described_paths(source, paths, included):
    """Repo files a changed page names, which the reviewer would otherwise never see.

    A review supplies the bytes of ``record["paths"]`` only. A changed
    documentation page that describes a source file it does not change therefore
    arrives without the code it documents, and the documented rules cannot be
    traced to code: measured on the docs releases 19fb7646 and c87cd884, where
    ``docs/network-view.md`` describes ``checks/network-care.py``'s RED/UNKNOWN
    arms and the reviewer answered "unknown" for want of that file.

    ``page_paths_text`` decodes the path tokens a page spells; here they are
    resolved against the release tree and returned as unchanged review entries;
    a token that escapes that tree is dropped, never read or handed over.
    Deterministic: first-appearance order, capped at ``DESCRIBED_PATH_MAX``;
    an over-size file is skipped, never truncated, and a file whose bytes are
    not UTF-8 is skipped too, because ``wall_patch.review`` decodes every entry
    as UTF-8 and one undecodable described file would otherwise lose the whole
    review as a ``review_error``.
    """
    found, seen, total = [], set(included), 0
    source_root = source.resolve()
    for name in paths:
        if not page_paths_text.is_page(name):
            continue
        file = source / name
        if not file.is_file() or file.is_symlink() or file.stat().st_size > _DESCRIBED_MAX_BYTES:
            continue
        for token in page_paths_text.referenced_paths(file.read_text(errors="replace")):
            if token in seen or token in paths:
                continue
            candidate = source / token
            if not candidate.resolve().is_relative_to(source_root):
                continue
            if not candidate.is_file() or candidate.is_symlink():
                continue
            size = candidate.stat().st_size
            if size > _DESCRIBED_MAX_BYTES or total + size > _DESCRIBED_TOTAL_BYTES:
                continue
            if not patch_text.is_utf8(candidate.read_bytes()):
                continue
            seen.add(token); found.append(token); total += size
            if len(found) >= DESCRIBED_PATH_MAX:
                return found
    return found


def review(home, identity):
    path = record_path(home,identity); record=read_json(path)
    if not record or (record["phase"] not in ("delivered", "reverting", "reverted") and record.get("observed") is not True):
        raise ValueError("review needs a delivered or reverted committed release")
    if record.get("review_pending") is False:
        return dict(phase="reviewed", id=identity)
    queued = Path(home)/"work/reviews"/f"{identity}.result.json"
    if queued.exists():
        prior = read_json(queued)
        if "review_error" in prior.get("result", {}):
            # An explicit retry is a new attempt; retain the unavailable result.
            # A wake can defer this error's merge, so an unmerged failed result
            # also belongs here: merging it would only record the error and
            # return without re-running the model.
            archive = queued.parent/f"{identity}-{time.time_ns()}.failed-attempt.json"
            write_json(archive, prior)
        else:
            merge_review(home, queued)
            current = read_json(path)
            return dict(phase="reviewed" if current.get("review_pending") is False else "review-pending", id=identity)
    source=Path(record["source"]); old=Path(home)/"releases"/record["previous"]
    patch=dict(runtime=str(old),phase="ordinary-delivered-awaiting-review",test=record["check"],files={})
    log=Path(home)/"work/reviews"/f"{record['sha']}.test.txt"; log.parent.mkdir(parents=True,exist_ok=True)
    log.write_text(record["check"]["output"]); patch["test"]=dict(record["check"],log=str(log))
    for name in record["paths"]:
        before=wall_patch.snapshot(old/name); after=wall_patch.snapshot(source/name)
        patch["files"][name]=dict(before=before,runtime_before=before,after=after)
    # A changed page that describes a source file it does not change would
    # otherwise reach the reviewer without the code it documents. Hand those
    # files over as unchanged entries (an empty diff still carries full
    # after_text), so the documented rules can be traced to their source.
    for name in _described_paths(source, record["paths"], patch["files"]):
        same=wall_patch.snapshot(source/name)
        patch["files"][name]=dict(before=same,runtime_before=same,after=same)
    # Reviewer understanding depends on the author's stated intent (Bacchelli
    # §VI.B: rationale determines what a reviewer can judge). The generic
    # "probe succeeded" line cannot explain a design choice in the diff, and a
    # reviewer measured on ac94997b could not confirm the intent behind a
    # pre-existing comment. Supply the release window's own commit messages.
    intent=_release_intent(record["source"],record["previous"],record["sha"])
    patch["rationale"]=("Asynchronous ordinary scoped release review. Tests passed and actual caller probe succeeded. Review for concrete source defects; recovery configuration and probe evidence are retained."
        + (f"\n\nAuthor intent — the commits in this release:\n{intent}" if intent else ""))
    patch["review_kind"]="ordinary-post-delivery"
    # The review question asks about the release's recovery path, so supply the
    # retained evidence it names rather than only asserting it is retained: the
    # previous release, the observation argv actually run, the automatic rollback
    # flag and the explicit revert command that restores config, re-points the pin
    # and re-runs the probe.
    patch["recovery"]=dict(
        previous=record["previous"],
        rollback_available=record.get("rollback_available"),
        observation_argv=record.get("observe_argv"),
        observation_code=(record.get("observe") or {}).get("code"),
        revert_argv=[sys.executable,"-m","mishe_tauftauf","--home",str(home),"release","revert","--id",identity],
    )
    try:
        result = dict(review=wall_patch.review(Path(home),patch))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        result = dict(review_error=str(exc))
    # Persist before trying the shared lock. Never lose a completed model call
    # because a delivery is running, and never write a stale delivery transition.
    write_json(queued, dict(id=identity, result=result, merged=False))
    # merge_review records the error and its retry state; when a delivery or
    # activation holds the lock exclusively it defers both, and the work-watch
    # poll's later merge does the same.
    merge_review(home, queued)
    current = read_json(path)
    return dict(phase="reviewed" if current.get("review_pending") is False else "review-pending", id=identity)


REVIEW_MERGE_RETRY_S = 30
"""Per-result fallback lock wait when merge_review is called directly."""

REVIEW_MERGE_BUDGET_S = 20
"""Total lock-wait time one work poll spends across all review merges.

The work-watch producer gives `work poll` 45s (signal run --timeout 45). A
per-result 30s retry times out the whole poll once a few results are pending
behind an exclusive delivery or activation hold, flapping
failed-mishe-producer-work-watch-service RED. Bound the batch instead: a busy
merge still retries within it, and pending results are retried on the next poll.
"""


def merge_review(home, queued, *, sleep=time.sleep, clock=time.monotonic,
                 retry_s=REVIEW_MERGE_RETRY_S, deadline=None):
    outcome = read_json(queued)
    if not outcome or outcome.get("merged"):
        return
    if deadline is None:
        deadline = clock() + retry_s
    try:
        while True:
            try:
                with review_records_lock(home), delivery_lock(home, shared=True):
                    path = record_path(home, outcome["id"])
                    current = read_json(path)
                    if not current:
                        raise ValueError("review target record disappeared")
                    result = outcome["result"]
                    if "review" in result and current.get("review_pending") is not False:
                        current.update(result, review_pending=False)
                        current["reviewed_at"] = time.time()
                        current["review_state"] = "clear" if result["review"]["clear"] else "action-needed"
                        if not result["review"]["clear"] and current["phase"] == "delivered":
                            from . import intentions
                            from .failure import default_notify
                            intentions.add(home, current["owner"], "review-" + current["sha"][:12],
                                           note="Decide a concrete fix or revert for independent release finding", due=time.time()+21600)
                            default_notify(home, current["owner"], f"Release {outcome['id']}: review finding; decide fix/revert within six hours. Evidence: {path}")
                    elif "review_error" in result:
                        current["review_error"] = result["review_error"]
                        schedule_review_retry_if_needed(home, current)
                    write_json(path, current)
                    shared_path = review_queue_path(home, current)
                    shared = read_json(shared_path)
                    if shared is not None:
                        shared.update(result=result, state="reviewed" if "review" in result else "unavailable")
                        write_json(shared_path, shared)
                        for identity in shared.get("release_ids", [shared["release_id"]]):
                            target = shared_path.parent/f"{identity}.result.json"
                            if identity != outcome["id"] and not target.exists():
                                write_json(target, dict(id=identity, result=result, merged=False))
                    write_json(queued, dict(outcome, merged=True))
                return
            except DeliveryBusy:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise
                sleep(min(0.5, remaining))
    except ValueError as exc:
        # Busy dispatch is an expected pending result; malformed evidence stays
        # visible in the retained queue and the next poll retries the merge.
        write_json(queued, dict(outcome, merge_error=str(exc)))


def merge_reviews(home, **kwargs):
    clock = kwargs.pop("clock", time.monotonic)
    budget_s = kwargs.pop("budget_s", REVIEW_MERGE_BUDGET_S)
    deadline = clock() + budget_s
    for queued in sorted((Path(home)/"work/reviews").glob("*.result.json")):
        merge_review(home, queued, clock=clock, deadline=deadline, **kwargs)
