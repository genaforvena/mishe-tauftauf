"""Committed ordinary delivery, with functional probes and automatic recovery.

The mechanism and all scheduler/protocol changes require the strong path.
No legacy delivery_verified flag is minted here. A promotion affects only
future consumers, under the same lock held across a running wake.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from . import release_boundary, work_boundary as boundary, wall_patch
from .state_files import read_json, write_json
from .work_receipts import digest


# Everything outside the admitted leaf/docs list defaults to strong. The
# live list lives in work_boundary; this module only delegates to it.
def classification(paths):
    return boundary.release_classification(paths)


def advance_candidates(repo, pin, head=None):
    """Commits on the default branch after ``pin``, oldest first."""
    return boundary.commits_after(repo, pin, head)


def ordinary_prefix(repo, pin, commits):
    """Newest commit whose whole ``pin..sha`` diff is ordinary, and where the walk stopped.

    Returns ``(sha, paths, stopped_by)``: ``sha`` is the newest ordinary commit
    reachable from ``pin`` (``pin`` itself when the first commit is strong),
    ``paths`` the cumulative changed paths from ``pin`` to ``sha``, and
    ``stopped_by`` the first strong-class commit sha, or ``None`` when the whole
    range is ordinary. A strong commit is never stepped over: the ordinary
    commits before it advance, and it waits for its own fresh review instead of
    blocking them (plan pin-flow-20261108 item 1).
    """
    previous, paths = pin, []
    for commit in commits:
        changed = boundary.changed_paths(repo, previous, commit)
        if classification(changed) == "strong":
            return previous, paths, commit
        paths.extend(changed)
        previous = commit
    return previous, paths, None


def advance_target(repo, pin, *, ci_state, commits=None, head=None):
    """The commit ``mishe release advance`` may point the pin at, and why nothing newer.

    Walks the ordinary prefix newest-first and returns the newest commit whose
    exact-SHA CI passed; earlier ordinary commits ride along in one atomic point.
    ``ci_state(sha)`` is ``"pass"``/``"fail"``/``"pending"``/``"unknown"`` and
    anything but ``"pass"`` is ineligible, so UNKNOWN CI never advances a commit.
    Returns ``(sha, reason)`` with ``sha`` ``None`` when nothing may advance and
    ``reason`` naming the first blocker for a reader.
    """
    commits = advance_candidates(repo, pin, head) if commits is None else list(commits)
    target, _paths, stopped = ordinary_prefix(repo, pin, commits)
    if target == pin:
        return None, ("blocked-by-strong:" + stopped) if stopped else "pin-is-head"
    prefix = commits[:commits.index(target) + 1]
    for commit in reversed(prefix):
        if ci_state(commit) == "pass":
            if stopped:
                return commit, "advance-stops-before-strong:" + stopped
            return commit, (None if commit == target else "newest-ordinary-commit-ci-not-passing")
    return None, "no-ordinary-commit-with-passing-ci"


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


def run(argv, *, cwd, env):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
        return dict(argv=argv, code=result.returncode, output=result.stdout + result.stderr, at=time.time())
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(argv=argv, code=None, output=str(exc), at=time.time())


def closure(source, env):
    check = source / "checks/promotion-import-closure.py"
    if not check.is_file(): raise ValueError("committed import closure checker missing")
    result = run([sys.executable, str(check), "--home", env["MISHE_SEED_HOME"], "--repo", str(source)], cwd=source, env=env)
    if result["code"] != 0: raise ValueError("committed import closure failed: " + result["output"])


@contextmanager
def delivery_lock(home):
    from .core.state import DispatchStore
    from .delivery_requests import check_admission
    check_admission(home)
    path = DispatchStore(Path(home) / "dispatch").lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: raise DeliveryBusy("running wake or promotion holds dispatch lock; promote after wake settles") from None
        check_admission(home)
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DeliveryBusy(ValueError):
    """No effect: the existing dispatch exclusion is owned elsewhere."""


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


def review_queue_path(home, record):
    # The same SHA from a different base has a different scoped diff to read.
    key = record["sha"] + ("-" + record["previous"] if record.get("previous") else "")
    return Path(home)/"work/reviews"/f"{key}.json"


def promote(home, repo, sha, *, check, observe, previous, activate=None, probe=run,
            reviewer=None, closure=closure, strong_patch=None, request_id=None):
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
    with delivery_lock(home):
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
        commands = wall_patch.command_artifacts(home, [check, observe])
        record["command_artifacts"] = commands
        record["observe_argv"] = observe
        from .roles import declared
        attributed_owner = boundary.commit_role(repo, sha)
        record["owner"] = attributed_owner if attributed_owner in declared(home) else "genome"
        record["check"] = run(check, cwd=source, env=env)
        if record["check"]["code"] != 0:
            record["phase"]="check-failed"; save(); return record
        closure(source, env)
        if not boundary.clean_tree(source) or boundary.head(source) != sha:
            record["phase"]="check-mutated-source"; save(); raise ValueError("committed source changed during checks")
        if commands != wall_patch.command_artifacts(home,[check,observe]):
            record["phase"]="check-mutated-command"; save(); raise ValueError("command changed during check")
        record["tests_passed"]=True
        configs = {str(p):wall_patch.snapshot(p) for p in config_paths(home)} if activate is None else {}
        record["config_before"]=configs; record["rollback_available"]=True; save()
        switch = activate or (lambda root, target: globals()["activate"](home,repo,root,target))
        try:
            record["activation_started_at"] = time.time(); save()
            switch(source,sha); record["activated"]=True; record["events"].append("activated"); save()
            if activate is None:
                record["consumer_invocation"] = boundary.invoke_arm_consumer(source/"src")
            record["observe"] = probe(observe,cwd=source,env=env)
            if record["observe"]["code"] != 0: raise ValueError("functional probe failed")
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
                record["rollback_probe"] = probe(observe,cwd=old,env=dict(env,PYTHONPATH=str(old/"src")))
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


def review(home, identity):
    path = record_path(home,identity); record=read_json(path)
    if not record or (record["phase"] not in ("delivered", "reverting", "reverted") and record.get("observed") is not True):
        raise ValueError("review needs a delivered or reverted committed release")
    if record.get("review_pending") is False:
        return dict(phase="reviewed", id=identity)
    queued = Path(home)/"work/reviews"/f"{identity}.result.json"
    if queued.exists():
        prior = read_json(queued)
        if prior.get("merged") and "review_error" in prior.get("result", {}):
            # An explicit retry is a new attempt; retain the unavailable result.
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
    patch["rationale"]="Asynchronous ordinary scoped release review. Tests passed and actual caller probe succeeded. Review for concrete source defects; recovery configuration and probe evidence are retained."
    try:
        result = dict(review=wall_patch.review(Path(home),patch))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        result = dict(review_error=str(exc))
    # Persist before trying the shared lock. Never lose a completed model call
    # because a wake is running, and never write a stale delivery transition.
    write_json(queued, dict(id=identity, result=result, merged=False))
    merge_review(home, queued)
    current = read_json(path)
    return dict(phase="reviewed" if current.get("review_pending") is False else "review-pending", id=identity)


def merge_review(home, queued):
    outcome = read_json(queued)
    if not outcome or outcome.get("merged"):
        return
    try:
        with delivery_lock(home):
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
    except ValueError as exc:
        # Busy dispatch is an expected pending result; malformed evidence stays
        # visible in the retained queue and the next poll retries the merge.
        write_json(queued, dict(outcome, merge_error=str(exc)))


def merge_reviews(home):
    for queued in sorted((Path(home)/"work/reviews").glob("*.result.json")):
        merge_review(home, queued)
