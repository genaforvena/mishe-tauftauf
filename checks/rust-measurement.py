#!/usr/bin/env python3
"""Detector: the cache-miss read cost that would reopen the next Rust slice.

The rust-plan `next-rust-slice` task is closed on one number (plan 10.39): the
forced full fold of a role's tape, `Feed(home).role_entries(role, _force_full=True)`,
measured in a fresh process. The re-open trigger is a cost above 5 CPU-s of the
Python reader or a peak RSS above 200 MB for any role. Until this detector
existed nothing ran that measurement on a schedule, so the slice could only
reopen by hand.

The 5 CPU-s bar stays on the Python reader, whose history (3.2-4.4 CPU-s) is the
calibration. The fold also spends CPU in the Rust children `feed.py` spawns, so
each reading records `children_cpu_s` and their sum `fold_cpu_s`, and the summary
prints reader, children and total. There is deliberately no bar on the total yet:
a forced fold at load1 4.34 on 3 CPUs measured senses children 19.6 s (total
23.6) against 3.1-3.6 s in eight quiet folds (plan 10.54/10.55), so a total bar
calibrated on quiet runs would flap under contention. Every run now records both
quantities in the history, which is where the total's own bar will be calibrated.

The forced fold is a cache-miss price, and the running consumer does not pay it
on an ordinary wake: `role_entries` serves a persisted role cache and extends it
by the bytes appended since (plan 10.70). So each role also gets a second child
that reads the way a wake does, and its `cpu_s` is the live cost the trigger now
also applies to. The two readings must agree on `view_digest` and `entries`; a
disagreement is RED, because it means the cached view a wake receives is not the
view the canonical tape rebuilds.

Each fold also records the verifier's V (served), N (reverified) and S (skipped)
decisions and whether it rebuilt the tape's frame index. These observations help
attribute child CPU outliers. A fold with no tape leaves both fields null.

This is a System 0 duty: a deterministic measurement, no judgment. Each role is
folded in its own child process, so CPU and peak RSS are not shared between
roles. The host guard comes first: a measurement made on a saturated host would
skew the numbers, so under the guard the run is skipped and reads UNKNOWN, never
GREEN.

The guard reads what actually distorts a CPU-time measurement, not ambient
activity: a saturation loadavg bar (one runnable task per online CPU), this
host's CPU and memory pressure (PSI `some avg10`), and the memory headroom
floor. The earlier flat `load1 > 2` bar skipped on the minds' own model drivers:
three forced folds under load1 2.93-3.29 came in at 3.207/3.421/3.869 CPU-s for
senses and 3.214/3.240/3.613 for genome against a 5.0 CPU-s trigger, the same
band as the quiet runs, while 21% of hourly runs were skipped (plan 10.41/10.44).
A 25% CPU-pressure bar then skipped the live pin at `some avg10` 32.85 with
load1 1.17; forced at `some avg10` 39.56 the same fold measured 4.161 CPU-s,
17% under the trigger, so the bar is 60 and only severe contention skips
(plan 10.45). An unreadable load, pressure or meminfo file is still UNKNOWN,
never GREEN.

Verdicts:
  GREEN   0  every role measured under both triggers, the live and rebuilt views
              agree, and the installed bytes match the release source.
  RED     2  a role is over a trigger (forced or live reader CPU, RSS), or the live
              view disagrees with the rebuilt one; the slice's pre-gate A/B may open.
  UNKNOWN 3  skipped by the host guard, a child failed, the site is missing, or
              the installed bytes could not be checked against the release.

The latest reading is written to an ignored artifact under the site home so a
person can read the raw numbers. Every run also appends one JSON line to
`rust-measurement-history.jsonl` next to it: a completed sample keeps its
readings and guard facts, a guard skip keeps the reason and the guard facts it
observed. The 60 bar in §10.45 rests on one measured point, so the history is
how the next calibration gets a curve instead of another forced fold; it is
bounded to the newest `HISTORY_MAX_LINES` lines.

Each run also checks the binary side of the same slice, because a CPU reading of
a path that cannot run says nothing. The Rust binaries live in `$SITE/bin` and a
pin move does not refresh them (plan 10.46), so a release can carry crate source
newer than the bytes the consumer execs. `git diff` from a binary's recorded
install commit to the release commit over that crate's `.rs`, `Cargo.toml`,
`Cargo.lock` and `build.rs` is positive drift: the reading then cannot stand for
the release's source, so the run is RED and names the rebuild. A record whose
sha256 no longer matches its bytes is RED too, since the resolver refuses those
bytes. A site with no `bin/` installs, a crate with no install record, a release
whose commit git cannot read, or an install commit git cannot resolve is
UNKNOWN (exit 3): unverified bytes cannot stand behind a GREEN about the path
that runs them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Installed binary under $SITE/bin : crate directory under rust/.
CRATES = {
    "mishe-cgroup-counters": "mishe-cgroup-counters",
    "mishe-network-probe": "mishe-network-probe",
    "mishe-records": "mishe-records",
    "mishe-signal-sample": "mishe-signal-samples",
    "mishe-systemd-show": "mishe-systemd-show",
    "mishe-tape": "mishe-tape",
    "mishe-tape-facts": "mishe-tape-facts",
    "mishe-unit-paths": "mishe-unit-paths",
}
# Files whose change alters the compiled bytes or the dependency graph they fix.
SOURCE_NAMES = ("Cargo.toml", "Cargo.lock", "build.rs")

DEFAULT_ROLES = ("senses", "genome")
DEFAULT_CPU_TRIGGER_S = 5.0  # the Python reader's own CPU; long history, 3.2-4.4 s
DEFAULT_RSS_TRIGGER_KB = 200 * 1024
DEFAULT_MAX_LOAD_FRAC = 1.0  # runnable tasks per online CPU; above it the host is saturated
# Percent of a 10 s window stalled on CPU. Measured: a fold at some avg10 39.56
# came in at 4.161 CPU-s, 17% under the 5.0 trigger, so pressure this high still
# leaves the verdict intact; only severe contention (>60%) skips. A 25 bar was
# calibrated on one quiet sample (some avg10 4.14) and skipped the live pin at
# some avg10 32.85 with load1 1.17 (plan 10.45).
DEFAULT_MAX_CPU_PRESSURE = 60.0
DEFAULT_MAX_MEMORY_PRESSURE = 10.0  # percent of a 10 s window stalled on memory
DEFAULT_MIN_FREE_KB = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S = 240
ARTIFACT_DIR = Path("artifacts") / "rust-plan-20261107"
HISTORY_MAX_LINES = 2000

CHILD = r"""
import hashlib, json, os, resource, subprocess, sys, time
from pathlib import Path

# Popen reaps children with os.waitpid, which discards their rusage. wait4
# returns the same wait result plus the reaped child's rusage: capture pid ->
# executable at spawn and fold each child's CPU into that executable's bucket.
# Popen serialises reaping under its own waitpid lock, so one child is handled
# at a time and the dict needs no second lock.
_exe_of_pid = {}
_child_cpu_by_exe = {}
_popen_init = subprocess.Popen.__init__

def _recording_popen_init(self, args, *a, **kw):
    _popen_init(self, args, *a, **kw)
    argv = args if isinstance(args, (list, tuple)) else str(args).split()
    _exe_of_pid[self.pid] = os.path.basename(str(argv[0])) if argv else "<unknown>"

def _wait4(pid, options):
    got, status, usage = os.wait4(pid, options)
    if got > 0:
        name = _exe_of_pid.pop(got, None)
        if name is not None:
            _child_cpu_by_exe[name] = (_child_cpu_by_exe.get(name, 0.0)
                                       + usage.ru_utime + usage.ru_stime)
    return got, status

if hasattr(os, "wait4"):
    subprocess.Popen.__init__ = _recording_popen_init
    os.waitpid = _wait4

from mishe_tauftauf.feed import Feed
import mishe_tauftauf.feed as feed_module

def child_cpu():
    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return u.ru_utime + u.ru_stime

def cpu_pressure_some():
    # PSI's `some avg10` is the percent of the last 10 s CPUs were contended.
    # Coarse beside one fold, but it separates a spike that rode host load from
    # one on a quiet host, which the pre-run guard alone cannot show.
    try:
        for line in open("/proc/pressure/cpu"):
            if line.startswith("some "):
                return float(line.split("avg10=", 1)[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return None

def memory_pressure_some():
    # Same PSI shape for memory. A CPU-time reading cannot show that reclaim or
    # swap-in stalled the fold, so the record keeps both sides of the fold.
    try:
        for line in open("/proc/pressure/memory"):
            if line.startswith("some "):
                return float(line.split("avg10=", 1)[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return None

def peak_rss_kb():
    # VmHWM is this process's own high-water mark after exec. ru_maxrss is not
    # usable here: across fork+exec Linux carries the parent's RSS into the
    # child's ru_maxrss, so a large launcher (plan 10.57) inflates it 3x.
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

home, role = Path(sys.argv[1]), sys.argv[2]
forced = (sys.argv[3] if len(sys.argv) > 3 else "forced") == "forced"
diagnostics = {}
p0 = cpu_pressure_some()
m0 = memory_pressure_some()
c0 = time.process_time(); k0 = child_cpu(); t0 = time.perf_counter()
rows = Feed(home).role_entries(role, _force_full=forced, _fold_diagnostics=diagnostics)
c1 = time.process_time(); k1 = child_cpu(); t1 = time.perf_counter()
p1 = cpu_pressure_some()
m1 = memory_pressure_some()
# The digest is taken after the timed window: hashing 36k rows is not fold work,
# and the two variants are compared by this value.
digest = hashlib.sha256("\n".join(
    json.dumps(row if isinstance(row, dict) else getattr(row, "__dict__", str(row)),
               sort_keys=True, default=str) for row in rows).encode()).hexdigest()[:16]
print(json.dumps({
    "role": role,
    "feed_path": str(Path(feed_module.__file__).resolve()),
    "entries": len(rows),
    "view_digest": digest,
    "cpu_s": round(c1 - c0, 3),
    "children_cpu_s": round(k1 - k0, 3),
    "child_cpu_by_exe": ({k: round(v, 3) for k, v in sorted(_child_cpu_by_exe.items())}
                         if hasattr(os, "wait4") else None),
    "record_decisions": diagnostics.get("record_decisions"),
    "checkpoint_rebuilt": diagnostics.get("checkpoint_rebuilt"),
    "role_cache_miss": diagnostics.get("role_cache_miss"),
    "cpu_pressure_some_avg10_before": p0,
    "cpu_pressure_some_avg10_after": p1,
    "memory_pressure_some_avg10_before": m0,
    "memory_pressure_some_avg10_after": m1,
    "fold_cpu_s": round((c1 - c0) + (k1 - k0), 3),
    "elapsed_s": round(t1 - t0, 3),
    "maxrss_kb": peak_rss_kb(),
}))
"""


def online_cpus() -> int:
    """The CPUs this process may run on, never zero."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def pressure_some_avg10(path: Path) -> float | None:
    """The `some avg10` stall percentage, or None when the PSI file is unreadable."""
    try:
        for line in path.read_text().splitlines():
            if not line.startswith("some "):
                continue
            for field in line.split()[1:]:
                if field.startswith("avg10="):
                    return float(field.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def host_guard(max_load: float | None, min_free_kb: int,
               max_cpu_pressure: float, max_memory_pressure: float
               ) -> tuple[str | None, dict]:
    """Return the reason the host is too loaded to measure, and the observed facts."""
    facts: dict = {}
    try:
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        return f"loadavg unreadable: {exc}", facts
    cpus = online_cpus()
    bar = max_load if max_load is not None else DEFAULT_MAX_LOAD_FRAC * cpus
    facts.update({"load1": round(load1, 2), "load_bar": round(bar, 2), "online_cpus": cpus})
    if load1 > bar:
        return f"loadavg {load1:.2f} above the {bar:g} bar over {cpus} CPUs", facts

    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError as exc:
        return f"meminfo unreadable: {exc}", facts
    avail = None
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1])
            break
    if avail is None:
        return "MemAvailable not found in meminfo", facts
    facts["mem_available_mb"] = avail // 1024
    if avail < min_free_kb:
        return f"MemAvailable {avail // 1024} MB below the {min_free_kb // 1024} MB floor", facts

    cpu_psi = pressure_some_avg10(Path("/proc/pressure/cpu"))
    if cpu_psi is None:
        return "cpu pressure unreadable (/proc/pressure/cpu)", facts
    facts["cpu_pressure_some_avg10"] = cpu_psi
    if cpu_psi > max_cpu_pressure:
        return f"cpu pressure some avg10 {cpu_psi:g} above the {max_cpu_pressure:g} bar", facts

    mem_psi = pressure_some_avg10(Path("/proc/pressure/memory"))
    if mem_psi is None:
        return "memory pressure unreadable (/proc/pressure/memory)", facts
    facts["memory_pressure_some_avg10"] = mem_psi
    if mem_psi > max_memory_pressure:
        return f"memory pressure some avg10 {mem_psi:g} above the {max_memory_pressure:g} bar", facts
    return None, facts


def _cache_state(role_cache_miss: bool | None) -> str:
    """Render the feed diagnostics flag; None means the fold never set it (no tape)."""
    if role_cache_miss is None:
        return "UNKNOWN"
    return "MISS" if role_cache_miss else "hit"


def fold_role(repo: Path, home: Path, role: str, timeout: int,
              variant: str = "forced") -> dict:
    """Measure one role's read in a fresh child process.

    ``forced`` rebuilds from the canonical tape, the original cache-miss price;
    ``live`` reads through the persisted role cache and the tape checkpoint, as
    a wake does.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    src = str(repo / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # Dont-write alone still permits reading timestamp-valid stale bytecode.
    # Give this child an empty cache so its imports come from release sources.
    with tempfile.TemporaryDirectory(prefix="mishe-fold-pycache-") as cache:
        env["PYTHONPYCACHEPREFIX"] = cache
        proc = subprocess.run(
            [sys.executable, "-c", CHILD, str(home), role, variant],
            capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{role} fold exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f"{role} fold printed nothing")
    return json.loads(lines[-1])


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one bounded read-only git query against the release worktree."""
    try:
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def binary_provenance(home: Path, repo: Path) -> dict:
    """Compare each installed Rust binary with the crate source in this release.

    Drift is source the release carries that the installed bytes were not built
    from; `broken` is an install record that no longer matches its bytes and that
    the resolvers therefore refuse. `unknown` names evidence this run could not
    read at all; it is reported and never turns into GREEN on its own. Every
    unreadable fact is named: one crate's unreadable record does not hide
    another's, because a person diagnosing the gap needs both.
    """
    facts: dict = {"checked": 0, "drift": [], "broken": [], "unknown": None, "digests": {}}
    missing: list[str] = []
    unknowns: list[str] = []
    bin_dir = home / "bin"
    if not bin_dir.is_dir():
        facts["unknown"] = f"no bin dir under {home}; no Rust installs to check"
        return facts
    head = _git(repo, "rev-parse", "--verify", "HEAD")
    pin = head.stdout.strip()
    if head.returncode != 0 or not pin:
        facts["unknown"] = f"release commit unreadable: {(head.stderr or head.stdout).strip()[:200]}"
        return facts
    for binary, crate in CRATES.items():
        record_path = bin_dir / f"{binary}.install.json"
        if not record_path.is_file():
            missing.append(binary)
            continue
        try:
            record = json.loads(record_path.read_text())
            recorded = str(record["sha256"])
            install_commit = str(record["source_commit"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            facts["broken"].append(f"{binary}: unreadable install record: {exc}")
            continue
        try:
            digest = hashlib.sha256((bin_dir / binary).read_bytes()).hexdigest()
        except OSError as exc:
            facts["broken"].append(f"{binary}: installed binary unreadable: {exc}")
            continue
        facts["checked"] += 1
        facts["digests"][binary] = digest
        if digest != recorded:
            facts["broken"].append(
                f"{binary}: sha256 {digest[:12]} != install record {recorded[:12]}")
        diff = _git(repo, "diff", "--quiet", install_commit, pin, "--",
                    f":(glob)rust/{crate}/*.rs", f":(glob)rust/{crate}/**/*.rs",
                    *(f"rust/{crate}/{name}" for name in SOURCE_NAMES))
        if diff.returncode == 1:
            facts["drift"].append(
                f"{binary}: rust/{crate} differs between install {install_commit[:8]} "
                f"and release {pin[:8]}")
        elif diff.returncode != 0:
            unknowns.append(f"{binary}: cannot compare {install_commit[:8]}..{pin[:8]}: "
                            f"{(diff.stderr or diff.stdout).strip()[:200]}")
    if missing:
        unknowns.append(f"{len(missing)} crate(s) with no install record, bytes "
                        f"unverified: {', '.join(missing)}")
    if unknowns:
        facts["unknown"] = "; ".join(unknowns)
    return facts


def release_identity(repo: Path) -> tuple[dict | None, str | None]:
    """Identify a clean source tree; an editable or unreadable tree is unknown."""
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    status = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", "src")
    if head.returncode or not head.stdout.strip() or status.returncode:
        return None, "release commit or source status unreadable"
    if status.stdout:
        return None, "release source has uncommitted or untracked changes"
    return {"path": str(repo.resolve()), "commit": head.stdout.strip()}, None


def append_history(home: Path, record: dict, max_lines: int = HISTORY_MAX_LINES) -> None:
    """Append one run to the bounded history beside the latest artifact.

    A history write failure must not change the duty's verdict: the sample and the
    signal already exist, so the error is printed and the run continues.
    """
    path = home / ARTIFACT_DIR / "rust-measurement-history.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        lines = path.read_text().splitlines()
        if len(lines) > max_lines:
            path.write_text("\n".join(lines[-max_lines:]) + "\n")
    except OSError as exc:
        print(f"rust-measurement: history not written: {exc.strerror or exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    parser.add_argument("--cpu-trigger", type=float, default=DEFAULT_CPU_TRIGGER_S)
    parser.add_argument("--rss-trigger-kb", type=int, default=DEFAULT_RSS_TRIGGER_KB)
    parser.add_argument("--max-load", type=float, default=None,
                        help="absolute load1 bar; default one runnable task per online CPU")
    parser.add_argument("--max-cpu-pressure", type=float, default=DEFAULT_MAX_CPU_PRESSURE)
    parser.add_argument("--max-mem-pressure", type=float, default=DEFAULT_MAX_MEMORY_PRESSURE)
    parser.add_argument("--min-free-kb", type=int, default=DEFAULT_MIN_FREE_KB)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--no-artifact", action="store_true")
    args = parser.parse_args(argv)

    home: Path = args.home
    requested_repo: Path = args.repo
    repo: Path = requested_repo.resolve()
    if not home.is_dir():
        print(f"rust-measurement: UNKNOWN site home missing: {home}", file=sys.stderr)
        return 3
    if not (repo / "src" / "mishe_tauftauf" / "feed.py").is_file():
        print(f"rust-measurement: UNKNOWN no feed.py under {repo}/src", file=sys.stderr)
        return 3

    skip, guard = host_guard(args.max_load, args.min_free_kb,
                             args.max_cpu_pressure, args.max_mem_pressure)
    if skip:
        if not args.no_artifact:
            append_history(home, {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "verdict": "skipped",
                "skip": skip,
                "guard": guard,
            })
        print(f"rust-measurement: UNKNOWN skipped, host busy: {skip}; re-run when quiet",
              file=sys.stderr)
        return 3

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    release, release_error = release_identity(repo)
    provenance = binary_provenance(home, repo)
    readings = []
    try:
        for role in roles:
            reading = fold_role(repo, home, role, args.timeout)
            live = fold_role(repo, home, role, args.timeout, "live")
            reading["live"] = {key: live[key] for key in
                               ("cpu_s", "children_cpu_s", "fold_cpu_s", "elapsed_s",
                                "maxrss_kb", "entries", "view_digest", "checkpoint_rebuilt",
                                "role_cache_miss")}
            readings.append(reading)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"rust-measurement: UNKNOWN fold failed: {exc}", file=sys.stderr)
        return 3

    release_after, after_error = release_identity(repo)
    binaries_after = binary_provenance(home, repo)
    release_errors = list(dict.fromkeys(filter(None, (release_error, after_error))))
    if requested_repo.resolve() != repo or release != release_after:
        release_errors.append("release changed during sampling")
    if provenance != binaries_after:
        release_errors.append("installed binary provenance changed during sampling")
    expected_feed = str((repo / "src/mishe_tauftauf/feed.py").resolve())
    if any(r.get("feed_path") != expected_feed for r in readings):
        release_errors.append("fold imported feed from outside the release")
    release_error = "; ".join(release_errors) or None

    over = []
    disagree = []
    for r in readings:
        live = r["live"]
        if r["cpu_s"] > args.cpu_trigger:
            over.append(f"{r['role']} reader cpu {r['cpu_s']} CPU-s > {args.cpu_trigger:g}")
        if live["cpu_s"] > args.cpu_trigger:
            over.append(f"{r['role']} live reader cpu {live['cpu_s']} CPU-s > {args.cpu_trigger:g}")
        if r["maxrss_kb"] > args.rss_trigger_kb:
            over.append(f"{r['role']} rss {r['maxrss_kb'] // 1024} MB > "
                        f"{args.rss_trigger_kb // 1024} MB")
        if live["view_digest"] != r["view_digest"] or live["entries"] != r["entries"]:
            disagree.append(f"{r['role']}: cached view {live['view_digest']} "
                            f"({live['entries']} entries) != rebuilt {r['view_digest']} "
                            f"({r['entries']} entries)")
    summary = "; ".join(f"{r['role']} reader {r['cpu_s']} + children "
                        f"{r['children_cpu_s']} = {r['fold_cpu_s']} CPU-s "
                        f"{r['maxrss_kb'] // 1024} MB {r['entries']} entries, live "
                        f"{r['live']['fold_cpu_s']} CPU-s view "
                        f"{'equal' if r['live']['view_digest'] == r['view_digest'] else 'DIFFERS'}"
                        f", cache {_cache_state(r['live']['role_cache_miss'])}"
                        for r in readings)
    by_exe = "; ".join(
        f"{r['role']}: " + ", ".join(f"{name} {secs:g}" for name, secs in r["child_cpu_by_exe"].items())
        for r in readings if r.get("child_cpu_by_exe"))
    if by_exe:
        summary += f" [child cpu: {by_exe}]"
    drift = provenance["drift"]
    broken = provenance["broken"]
    unknown = "; ".join(filter(None, (provenance["unknown"], release_error))) or None
    binaries = (f"{provenance['checked']} checked"
                + (f", {len(drift)} drift" if drift else "")
                + (f", {len(broken)} broken" if broken else "")
                + (f", unknown: {unknown}" if unknown else ""))

    if not args.no_artifact:
        measured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        out = home / ARTIFACT_DIR / "rust-measurement-latest.json"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "measured_at": measured_at,
                "readings": readings,
                "release": release,
                "provenance_error": release_error,
                "triggers": {"cpu_s": args.cpu_trigger, "rss_kb": args.rss_trigger_kb},
                "guard": guard,
                "over": over,
                "disagree": disagree,
                "binaries": provenance,
            }, indent=1) + "\n")
        except OSError as exc:
            print(f"rust-measurement: artifact not written: {exc.strerror or exc}", file=sys.stderr)
        append_history(home, {
            "measured_at": measured_at,
            "verdict": "red" if over or disagree or drift or broken else
                       ("unknown" if unknown else "green"),
            "release": release,
            "provenance_error": release_error,
            "readings": readings,
            "guard": guard,
            "over": over,
            "disagree": disagree,
            "binaries": provenance,
        })

    if disagree:
        print("rust-measurement: RED the cached role view a wake receives is not the "
              f"view the canonical tape rebuilds: {'; '.join(disagree)}", file=sys.stderr)
        return 2
    if drift or broken:
        print("rust-measurement: RED installed Rust bytes do not match the release source: "
              f"{'; '.join(broken + drift)}", file=sys.stderr)
        return 2
    if over:
        print(f"rust-measurement: RED trigger hit, slice may reopen: {'; '.join(over)} [{summary}]",
              file=sys.stderr)
        return 2
    if unknown:
        print("rust-measurement: UNKNOWN provenance unverified, the CPU reading "
              f"cannot be trusted as GREEN: {unknown} [{summary}]", file=sys.stderr)
        return 3
    print(f"rust-measurement: GREEN under triggers, slice stays closed: {summary} "
          f"[binaries {binaries}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
