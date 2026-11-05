"""Committed ordinary delivery, with functional probes and automatic recovery.

The mechanism and all scheduler/protocol changes require the strong path.
No legacy delivery_verified flag is minted here. A promotion affects only
future consumers, under the same lock held across a running wake.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from . import work_boundary as boundary, wall_patch
from .state_files import read_json, write_json
from .work_receipts import digest

# Everything outside a deliberately small leaf/docs list defaults to strong.
ORDINARY = {"src/mishe_tauftauf/producers.py"}


def classification(paths):
    return boundary.release_classification(paths)


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
    for name, values in record["files"].items():
        if wall_patch.snapshot(Path(source)/name) != values["after"]:
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
    path = DispatchStore(Path(home) / "dispatch").lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: raise ValueError("running wake or promotion holds dispatch lock; promote after wake settles") from None
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def config_paths(home):
    unit_dir = Path.home() / ".config/systemd/user"
    from .release_boundary import owned_runtime_units
    # A shared filename prefix does not grant ownership of host or peer units.
    return [Path(home)/"bin/mishe", Path(home)/"coordination-mode.json", Path(home)/"units-installed",
            Path(home)/"resolvers.generated.json",
            *owned_runtime_units(home, unit_dir)]


def reload_units():
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=30)


def restore_configuration(home, before):
    """Remove newly created triggers before restoring the prior exact files."""
    from .release_boundary import trigger_unit
    for path in config_paths(home):
        if str(path) not in before:
            if trigger_unit(path):
                subprocess.run(["systemctl", "--user", "disable", "--now", path.name],
                               check=True, capture_output=True, timeout=30)
            wall_patch.restore(path, None)
    for name, value in before.items():
        wall_patch.restore(Path(name), value)
    reload_units()


def activate(home, repo, source, sha):
    from .runtime_cli import run_units
    import argparse
    # Preserve existing trigger state; a newly declared owned trigger must
    # actually start. Recovery removes it and stops it if activation fails.
    previous_paths = set(config_paths(home))
    marker = wall_patch.snapshot(Path(home) / "units-installed")
    args = argparse.Namespace(command="sync", repo=repo, source_repo=source, pythonpath=str(source/"src"),
        unit_dir=Path.home()/".config/systemd/user", prefix="mishe", no_slices=False,
        no_systemctl=False, no_enable=True, uninstall=False)
    run_units(args, Path(home), time.time())
    from .release_boundary import trigger_unit
    for path in config_paths(home):
        if path not in previous_paths and trigger_unit(path):
            subprocess.run(["systemctl", "--user", "enable", "--now", path.name],
                           check=True, capture_output=True, timeout=30)
    wall_patch.restore(Path(home)/"units-installed", marker)
    wrapper = Path(home)/"bin/mishe"
    # No shell interpolation of paths: shlex is used only for the installed POSIX wrapper.
    from .release_boundary import wrapper_text
    wrapper.write_text(wrapper_text(source, sys.executable)); wrapper.chmod(0o755)
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
            reviewer=None, closure=closure, strong_patch=None):
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
            switch(source,sha); record["activated"]=True; record["events"].append("activated"); save()
            if activate is None:
                record["consumer_invocation"] = boundary.invoke_arm_consumer(source/"src")
            record["observe"] = probe(observe,cwd=source,env=env)
            if record["observe"]["code"] != 0: raise ValueError("functional probe failed")
            record["config_after"] = {str(p):wall_patch.snapshot(p) for p in config_paths(home)} if activate is None else {}
            record.update(observed=True,phase="delivered"); save()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record["failure"] = str(exc); record["events"].append("rollback-started"); save()
            record["rollback_at"] = time.time()
            try:
                old = materialize(home,repo,previous)
                if activate is not None: switch(old,previous)
                else:
                    restore_configuration(home, configs)
                    record["restored_consumer_invocation"] = boundary.invoke_arm_consumer(old/"src")
                record["rollback_probe"] = probe(observe,cwd=old,env=dict(env,PYTHONPATH=str(old/"src")))
                record["phase"]="rolled-back" if record["rollback_probe"]["code"]==0 else "UNKNOWN"
            except (OSError,ValueError,subprocess.SubprocessError) as restore_error:
                record.update(phase="UNKNOWN",rollback_error=str(restore_error))
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
        for name, value in record.get("config_after", {}).items():
            if wall_patch.snapshot(Path(name)) != value: raise ValueError("consumer configuration changed since promotion")
        try:
            restore_configuration(home, record["config_before"])
            old = Path(home)/"releases"/record["previous"]
            record["restored_consumer_invocation"] = boundary.invoke_arm_consumer(old/"src")
            record["revert_probe"] = run(record["observe_argv"], cwd=old,
                env=dict(os.environ, PYTHONPATH=str(old/"src"), MISHE_SEED_HOME=str(home)))
            record["phase"] = "reverted" if record["revert_probe"]["code"] == 0 else "UNKNOWN"
            record["reverted_at"] = time.time()
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            record.update(phase="UNKNOWN",revert_error=str(exc))
        write_json(path,record)
        return record


def review(home, identity):
    path = record_path(home,identity); record=read_json(path)
    if not record or record["phase"] not in ("delivered", "reverted"):
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
