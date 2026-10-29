#!/usr/bin/env python3
"""Prepare, activate and exercise exact-byte recovery delivery for one site."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

from mishe_tauftauf import recovery_io as recovery
from mishe_tauftauf.observations import validate_home

MODULES = ("recovery_policy.py", "recovery_io.py", "render_admission.py", "host_capacity.py", "probe_supervisor.py")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def replace(path, raw, mode):
    temporary = path.with_name(path.name + ".recovery-new")
    temporary.write_bytes(raw)
    temporary.chmod(mode)
    temporary.replace(path)


def prepare_renderers(home, snapshot, previous=None):
    rows = {}
    excluded = {}
    originals = snapshot / "renderers-before"
    originals.mkdir(exist_ok=True)
    for top in sorted((home / "top-pains").iterdir()):
        if top.is_symlink() or not top.is_file() or not os.access(top, os.X_OK):
            continue
        recovery.identity(top.name)
        raw = top.read_bytes()
        prior = (previous or {}).get("renderers", {}).get(top.name)
        if prior:
            if digest(raw) not in {prior["after_sha256"], prior["before_sha256"]}:
                raise ValueError(f"renderer changed before upgrade: {top.name}")
            raw = base64.b64decode(prior["before"])
        if top.name == "operator":
            # Its presentation supervisor regenerates this launcher during
            # recovery. Do not compete with that independently owned writer.
            excluded[top.name] = dict(prior or {}, reason="operator presentation supervisor regenerates its own renderer")
            continue
        mode = top.stat().st_mode & 0o777
        before = originals / top.name
        if before.exists() and before.read_bytes() != raw:
            raise ValueError("renderer backup already exists with different bytes")
        before.write_bytes(raw)
        before.chmod(mode)
        quoted = shlex.quote
        wrapper = ("#!/bin/sh\n"
                   "export MISHE_RECOVERY_PREVIOUS_PYTHONPATH_SET=\"${PYTHONPATH+x}\"\n"
                   "export MISHE_RECOVERY_PREVIOUS_PYTHONPATH=\"${PYTHONPATH-}\"\n"
                   f"export PYTHONPATH={quoted(str(snapshot / 'src'))}\n"
                   f"exec /usr/bin/python3 -m mishe_tauftauf.render_admission --home {quoted(str(home))} --role {quoted(top.name)} -- {quoted(str(before))}\n").encode()
        rows[top.name] = {"before": base64.b64encode(raw).decode(), "before_sha256": digest(raw),
                          "after": base64.b64encode(wrapper).decode(), "after_sha256": digest(wrapper), "mode": mode}
        if prior:
            rows[top.name]["upgrade_from"] = prior["after_sha256"]
    return {"snapshot": str(snapshot), "renderers": rows, "excluded": excluded}


def renderer_check(home, manifest, *, wrapped):
    for role, row in manifest["renderers"].items():
        path = home / "top-pains" / role
        key = "after" if wrapped else "before"
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != row[key + "_sha256"]:
            raise ValueError(f"renderer changed: {role}; preserve peer bytes")


def wrap_renderers(home, manifest):
    for role, row in manifest["renderers"].items():
        path = home / "top-pains" / role
        if path.is_symlink() or digest(path.read_bytes()) not in {row["before_sha256"], row["after_sha256"], row.get("upgrade_from")}:
            raise ValueError(f"renderer changed: {role}")
    for role, row in manifest["renderers"].items():
        replace(home / "top-pains" / role, base64.b64decode(row["after"]), row["mode"])


def restore_renderers(home, manifest):
    renderer_check(home, manifest, wrapped=True)
    for role, row in manifest["renderers"].items():
        replace(home / "top-pains" / role, base64.b64decode(row["before"]), row["mode"])


def command(argv, timeout=15):
    row = recovery.run_probe(argv, timeout=timeout)
    if row["ok"] is not True:
        raise ValueError(f"delivery command failed: {argv}: {row}")
    return row


def units(home, snapshot, session):
    prefix = f"{session}-recovery"
    run = f"/usr/bin/python3 -m mishe_tauftauf.recovery_io --home {shlex.quote(str(home))}"
    common = (f"Environment=PYTHONPATH={snapshot / 'src'}\n"
              f"Environment=MISHE_SEED_HOME={home}\n"
              "MemoryAccounting=yes\nMemoryMax=192M\nTasksMax=16\nNice=5\n")
    service = ("[Unit]\nDescription=Mishe independent functional recovery and render admission\n"
               "[Service]\nType=simple\n" + common + f"ExecStart={run} watch\n"
               "Restart=on-failure\nRestartSec=5\nWatchdogSec=45\nTimeoutStopSec=3\n"
               "[Install]\nWantedBy=default.target\n")
    result = {prefix + ".service": service}
    for suffix, action, schedule in (("guard", "guard", "OnBootSec=15\nOnUnitActiveSec=15"),
                                     ("outbox", "deliver", "OnBootSec=20\nOnUnitActiveSec=20"),
                                     ("evaluation", "evaluate", "OnActiveSec=1h\nOnUnitActiveSec=24h")):
        name = prefix + "-" + suffix
        # Publication cold-reads the canonical tape and may wait for a writer.
        # It must not inherit the sampler's tiny memory/time/task budget.
        budget = common.replace("MemoryMax=192M", "MemoryMax=1536M").replace("TasksMax=16", "TasksMax=32") if suffix == "outbox" else common
        timeout = 60 if suffix == "outbox" else 12
        result[name + ".service"] = ("[Unit]\nDescription=Mishe recovery " + suffix + "\n"
                                      "[Service]\nType=oneshot\n" + budget + f"ExecStart={run} {action}\nTimeoutStartSec={timeout}\n")
        result[name + ".timer"] = ("[Unit]\nDescription=Mishe recovery " + suffix + " schedule\n"
                                    "[Timer]\n" + schedule + "\nAccuracySec=1s\n"
                                    "[Install]\nWantedBy=timers.target\n")
    return result


def prepare(home, repo, *, upgrade=False):
    root = home / "recovery"
    previous = recovery.read_json(root / "deployment.json") if upgrade else None
    if (root / "deployment.json").exists() and not upgrade:
        raise ValueError("deployment already prepared; use its activate/revert/verify commands")
    sha = command(["git", "-C", str(repo), "rev-parse", "HEAD"])["stdout"].strip()
    contents = {name: (repo / "src/mishe_tauftauf" / name).read_bytes() for name in MODULES}
    identifier = digest(b"".join(contents.values()) + sha.encode())[:20]
    snapshot = home / "recovery-runtime" / (identifier + "-" + str(time.time_ns()))
    snapshot.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", sha, "src"], timeout=15)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        if any(member.name.startswith("/") or ".." in Path(member.name).parts or member.issym() or member.islnk() for member in tar.getmembers()):
            raise ValueError("unexpected archive member")
        tar.extractall(snapshot)
    for name, raw in contents.items():
        (snapshot / "src/mishe_tauftauf" / name).write_bytes(raw)
    manifest = prepare_renderers(home, snapshot, previous)
    session = (home / ".seed-raised").read_text().split()[0]
    recovery.identity(session)
    manifest.update(version=1, base_sha=sha, prepared_at=time.time(),
                    sources={name: digest(raw) for name, raw in contents.items()},
                    units=units(home, snapshot, session), session=session,
                    phase="prepared")
    if previous:
        recovery.save(root / "deployments" / f"{time.time_ns()}.json", previous, immutable=True)
        manifest["previous_units"] = previous["units"]
    config = {"interval": 15, "stable_seconds": 120, "deadline": 300,
              "render_slots": 1, "render_cooldown": 30, "probes": []}
    if (root / "config.json").exists() and not upgrade:
        raise ValueError("recovery config already exists; inspect before replacing")
    if not upgrade:
        recovery.save(root / "config.json", config)
    recovery.save(root / "deployment.json", manifest)
    if upgrade:
        return manifest
    samples = []
    for _ in range(10):
        observation = recovery.pressure()
        control = recovery.seed_lock_probe(home)
        samples.append(dict(observation, at=time.time(), control_ok=control["ok"], control_seconds=control["seconds"]))
        time.sleep(.1)
    recovery.save(root / "baseline.json", {"activated_at": time.time(), "samples": samples,
                                           "scope": "post-reboot baseline; pre-reboot observations retained separately"}, immutable=True)
    return manifest


def source_check(manifest):
    snapshot = Path(manifest["snapshot"])
    for name, expected in manifest["sources"].items():
        if digest((snapshot / "src/mishe_tauftauf" / name).read_bytes()) != expected:
            raise ValueError("reviewed recovery snapshot changed")


def coverage_units(coverage, enabled):
    """The dashboard's resident-service inventory, ``.service`` names only.

    Every reader of ``health/services.json`` — ``wall_view._service_block``,
    ``substrate_watch`` and the health pane's own ``seed_culture_views`` check —
    rejects the whole file unless every entry ends in ``.service``. One ``.timer``
    entry therefore leaves the health pane's service block and the substrate
    runtime UNKNOWN for every mind. Timers are scheduled units, not residents, so
    they stay out of the inventory; entries already present that are not services
    are dropped, so a manifest poisoned by an earlier activation heals instead of
    persisting across replants.
    """
    names = {name for name in coverage if isinstance(name, str) and name.endswith(".service")}
    names.update(name for name in enabled if name.endswith(".service"))
    return sorted(names)


def activate(home, manifest):
    source_check(manifest)
    config_dir = Path.home() / ".config/systemd/user"
    for name, text in manifest["units"].items():
        path = config_dir / name
        if path.exists() and path.read_text() not in {text, manifest.get("previous_units", {}).get(name)}:
            raise ValueError(f"existing unit changed: {name}")
    for role, row in manifest.get("excluded", {}).items():
        if "after_sha256" in row:
            path = home / "top-pains" / role
            current = digest(path.read_bytes())
            if current == row["after_sha256"]:
                replace(path, base64.b64decode(row["before"]), row["mode"])
            elif current != row["before_sha256"]:
                raise ValueError(f"excluded renderer changed: {role}")
    wrap_renderers(home, manifest)
    for name, text in manifest["units"].items():
        (config_dir / name).write_text(text)
    command(["systemctl", "--user", "daemon-reload"])
    enabled = [name for name in manifest["units"] if name.endswith(".timer") or name == f"{manifest['session']}-recovery.service"]
    command(["systemctl", "--user", "enable", "--now", *enabled])
    if manifest.get("previous_units"):
        command(["systemctl", "--user", "restart", f"{manifest['session']}-recovery.service"])
    coverage_path = home / "health/services.json"
    coverage = recovery.read_json(coverage_path)
    recovery.save(coverage_path, coverage_units(coverage, enabled))
    manifest.update(phase="active", activated_at=time.time())
    recovery.save(home / "recovery/deployment.json", manifest)


def revert(home, manifest):
    source_check(manifest)
    renderer_check(home, manifest, wrapped=True)
    config_dir = Path.home() / ".config/systemd/user"
    for name, text in manifest["units"].items():
        if (config_dir / name).read_text() != text:
            raise ValueError(f"unit changed: {name}")
    command(["systemctl", "--user", "disable", "--now", *manifest["units"]])
    restore_renderers(home, manifest)
    renderer_check(home, manifest, wrapped=False)
    manifest.update(phase="reverted", reverted_at=time.time())
    recovery.save(home / "recovery/deployment.json", manifest)


def observe(home, manifest):
    source_check(manifest)
    renderer_check(home, manifest, wrapped=True)
    command(["systemctl", "--user", "is-active", f"{manifest['session']}-recovery.service"])
    row = recovery.status(home)
    if ("reason" in row or row["checked_at"] < manifest["activated_at"]
            or not str(row["runtime"]).startswith(manifest["snapshot"] + "/")):
        raise ValueError(f"recovery activation not observed: {row}")
    return {"at": time.time(), "producer": "fresh", "functional_state": row["state"],
            "runtime": row["runtime"], "renderers": len(manifest["renderers"]),
            "scope": "delivery observation, not restoration of every functional incident"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("action", choices=["prepare", "upgrade", "activate", "revert", "verify", "observe"])
    args = parser.parse_args()
    home = validate_home(args.home)
    if args.action in {"prepare", "upgrade"}:
        row = prepare(home, Path(__file__).resolve().parents[1], upgrade=args.action == "upgrade")
    else:
        row = recovery.read_json(home / "recovery/deployment.json")
        if args.action == "activate":
            activate(home, row)
        elif args.action == "revert":
            revert(home, row)
        elif args.action == "observe":
            print(json.dumps(observe(home, row), sort_keys=True))
            return
        else:
            original_observation = observe(home, row)
            # Exercise one real consumer while all other expensive renderers
            # remain gated; simultaneous legacy rendering would recreate the
            # overload this recovery path is intended to contain.
            role = "operator" if "operator" in row["renderers"] else next(iter(row["renderers"]))
            target = home / "top-pains" / role
            saved = row["renderers"][role]
            renderer_check(home, row, wrapped=True)
            replace(target, base64.b64decode(saved["before"]), saved["mode"])
            reverted_at = time.time()
            consumer_env = dict(os.environ)
            runtime_release = recovery.read_json(home / "health/runtime-release.json")
            consumer_env["PYTHONPATH"] = str(Path(runtime_release["source"]) / "src")
            try:
                receipt = recovery.run_probe([str(target)], timeout=30, output_limit=1024 * 1024, env=consumer_env)
                probe_path = home / "recovery" / f"restored-renderer-{time.time_ns()}.json"
                recovery.save(probe_path, receipt, immutable=True)
            finally:
                if digest(target.read_bytes()) != saved["before_sha256"]:
                    raise ValueError("restored renderer changed during exercise; preserve peer bytes")
                replace(target, base64.b64decode(saved["after"]), saved["mode"])
            if receipt["ok"] is not True:
                raise ValueError(f"restored consumer failed; wrapper reapplied, evidence retained at {probe_path}")
            wrapped_receipt = recovery.run_probe([str(target)], timeout=35, output_limit=1024 * 1024, env=consumer_env)
            wrapped_path = home / "recovery" / f"reapplied-renderer-{time.time_ns()}.json"
            recovery.save(wrapped_path, wrapped_receipt, immutable=True)
            renderer_check(home, row, wrapped=True)
            deferred = (wrapped_receipt["code"] == 1
                        and wrapped_receipt["stdout"].startswith("UNKNOWN — render deferred:")
                        and wrapped_receipt["stderr"].startswith("UNKNOWN — render deferred:"))
            if wrapped_receipt["ok"] is not True and not deferred:
                raise ValueError(f"reapplied renderer failed without observed admission deferral: {wrapped_path}")
            service = f"{row['session']}-recovery.service"
            command(["systemctl", "--user", "stop", service])
            stopped = recovery.run_probe(["systemctl", "--user", "is-active", service], timeout=5)
            if stopped["code"] != 3 or stopped["stdout"].strip() != "inactive":
                raise ValueError("recovery controller backout not observed")
            restarted_at = time.time()
            command(["systemctl", "--user", "start", service])
            row["activated_at"] = restarted_at
            recovery.save(home / "recovery/deployment.json", row)
            cutoff = time.monotonic() + 30
            while time.monotonic() < cutoff:
                try:
                    reapplied_observation = observe(home, row)
                    break
                except ValueError:
                    time.sleep(.2)
            else:
                raise ValueError("reapplied producer unavailable")
            evidence = {"before": original_observation, "reverted_originals_observed_at": reverted_at,
                        "after": reapplied_observation, "sources": row["sources"],
                        "at": time.time(), "scope": "one actual renderer path live backout/reapply and independent controller stop/restart; full restore tested in fixtures",
                        "original_consumer": str(probe_path), "reapplied_consumer": str(wrapped_path),
                        "controller_inactive": stopped, "restarted_at": restarted_at}
            verification = home / "recovery" / f"delivery-verified-{time.time_ns()}.json"
            recovery.save(verification, evidence, immutable=True)
            row.update(phase="delivery-verified", verification=str(verification))
            recovery.save(home / "recovery/deployment.json", row)
    print(json.dumps({"phase": row["phase"], "snapshot": row["snapshot"]}))


if __name__ == "__main__":
    main()
