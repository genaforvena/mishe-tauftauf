#!/usr/bin/env python3
"""Detector: the write-once clause for bound evidence is not enforceable by mode.

The plant's clause makes a bound artifact write-once evidence. The outcome
records the file's digest, and the standing audit keeps the row forever. The
clause is enforced on *publication*: the writer refuses an ineligible path, a
symlink component, a lexical traversal and a path another outcome already
bound. Since commit ``d9f4ebe`` the writer also makes the file read-only at
the moment it binds it.

That last part is the only thing standing between a bound row and a later edit
that drops it permanently — and it does not hold.

A read-only **mode** protects an **inode**. Every writer in this plant that
saves a file does so by writing a temporary sibling and renaming it over the
target (``os.replace`` in ``seed._write_handoff``, ``cli.cmd_handoff``,
``checks.publish_report``, ``feed.Feed``), and a rename replaces the *path*,
not the inode the mode was applied to. The new file arrives with the writer's
own mode, so a bound file that was ``0444`` at publication is ``0644`` again
after the next save — and the row's recorded digest no longer matches the
bytes on disk. The mode can even be re-applied afterwards, so a file that
*looks* write-once now may already have lost its bytes.

This is not hypothetical. Measured on this plant (2026-10-29):

* the clause landed in ``3c22087`` and the read-only step in ``d9f4ebe``;
* of the **5442** outcomes published since the clause, **200** no longer match
  the digest they recorded;
* **23** of those were read-only at publication and were changed afterwards —
  the clause was applied and then defeated;
* **177** were published writable, so no clause was ever applied to them.

The second population is the one a mode can fix, and the plant's existing
``writable-bound-evidence`` check reports it. This check reports the first
population, which no mode can fix.

**What this check does not duplicate.** The plant's checks answer the
neighbouring questions and each keeps to its own:

* ``writable-bound-evidence.py`` asks whether a bound file is *writable now* —
  a row at risk, arriving before the loss;
* ``coverage-gap-remedy.py`` asks whether a lost row has a published remedy,
  within the coverage reader's window;
* ``check-cli-entry-root.py`` asks whether the *CLI* root carries the clause,
  by reading one root's bytes;
* this check asks whether the clause is **effective** — whether a bound file
  the clause made read-only survives the plant's own save path — and it
  measures that on every root that can carry a publication, by running the
  plant's own writers rather than by asserting what they ought to do.

**Two questions, kept apart because they have different owners.**

1. **Is the clause present on every root that can publish?** A root whose
   bytes predate ``d9f4ebe`` leaves every file it binds writable, so the
   clause is *absent* on that path rather than defeated. A pin advance fixes
   that; a mode does not.
2. **Where the clause is present, is it *effective*?** A root that applies the
   clause still loses the row when a later writer replaces the file. No pin
   advance fixes that, because the cause is the plant's own save path.

The first is answered by reading each root's bytes as **text** — the check
never imports or runs the plant package to decide it, so it cannot die because
an internal helper changed its signature. The second is answered by publishing
in a **disposable** home and then replacing the bound file the way the plant's
own writers do. A check that cannot decide says so.

Exit status:
  0  the clause is present on every root that can publish, and a bound file
     the clause made read-only survives the plant's own save path
  2  the clause is absent from a root that can publish, or a bound file the
     clause made read-only does not survive a replacement: the write-once
     property is not a property of the bytes
  3  the question cannot be decided: no root can be read, the probe cannot
     publish, or the plant's own save path cannot be found
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

#: The plant's package directory, relative to an import root.
PACKAGE = "mishe_tauftauf"

#: The module that publishes an outcome and applies the read-only step.
CLAUSE_MODULE = "wall.py"

#: The function that publishes the outcome. The clause is present when this
#: function exists and the module changes the evidence file's mode: a function
#: that only *read* the mode would not enforce anything.
CLAUSE_FUNCTION = "_publish_outcome"

#: The call that applies the mode change, matched by attribute name so a
#: renamed import of ``os`` still matches.
CLAUSE_CALL = "chmod"

#: The commit that introduced the write-once clause for bound evidence.
CLAUSE_COMMIT = "3c22087"

#: The commit that made the publishing writer set the bound file read-only.
READ_ONLY_COMMIT = "d9f4ebe"

#: The site's record of the pin every service is started from.
PIN_RECORD = "health/runtime-release.json"

#: The site's record of the roots it keeps off the pin on purpose.
DECLARED_RECORD = "health/declared-roots.json"

#: The site's CLI entry points. The first that names an import root wins.
ENTRY_POINTS = ("bin/mishe-tauftauf", "bin/mishe")

#: The name of the environment variable that carries a process's import root.
PYTHONPATH_ENV = "PYTHONPATH"

#: The directory the published record store lives in, relative to the home.
RECORDS_DIR = "records"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_text(path: Path) -> str:
    """The file's text, or the reason it cannot be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise Undecidable("%s cannot be read: %s" % (path, exc)) from exc


def _read_json(path: Path) -> dict:
    """A JSON object at path, or the reason it cannot be read."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecidable("%s cannot be read: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise Undecidable("%s does not hold a JSON object" % path)
    return payload


def carries_clause(root: Path) -> bool:
    """Whether the plant package at ``root`` applies the write-once clause.

    The clause is decided by reading the module as text. The check never
    imports the package: a measurement that imports the plant dies silently
    when an internal helper changes its signature, which is how a measurement
    script of discover's died on 2026-10-21.
    """
    module = root / PACKAGE / CLAUSE_MODULE
    if not module.is_file():
        return False
    text = _read_text(module)
    if CLAUSE_FUNCTION not in text:
        return False
    return CLAUSE_CALL in text


def _is_root(path: Path) -> bool:
    """Whether ``path`` is an import root of the plant package."""
    return (path / PACKAGE / "__init__.py").is_file()


def pinned_root(home: Path) -> Path | None:
    """The import root the plant's pin names, or None when it names none."""
    record = home / PIN_RECORD
    if not record.is_file():
        return None
    payload = _read_json(record)
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        return None
    root = Path(source)
    return root if _is_root(root) else None


def declared_roots(home: Path) -> dict[Path, str]:
    """The off-pin roots the site declares, mapped to the unit that owns them."""
    record = home / DECLARED_RECORD
    if not record.is_file():
        return {}
    payload = _read_json(record)
    units = payload.get("units")
    if not isinstance(units, dict):
        return {}
    roots: dict[Path, str] = {}
    for unit, entry in units.items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("root")
        if isinstance(value, str) and value:
            root = Path(value)
            if _is_root(root):
                roots[root] = str(unit)
    return roots


def entry_point_root(home: Path) -> tuple[Path, Path] | None:
    """The import root the site's CLI entry point exports, and the script."""
    for name in ENTRY_POINTS:
        script = home / name
        if not script.is_file():
            continue
        for line in _read_text(script).splitlines():
            line = line.strip()
            if not line.startswith("export %s=" % PYTHONPATH_ENV):
                continue
            value = line.split("=", 1)[1].strip().strip("\"'")
            for part in value.split(":"):
                if part and _is_root(Path(part)):
                    return Path(part), script
    return None


def live_roots() -> dict[Path, int]:
    """The import roots of the plant's own running processes, with counts.

    A root the site holds but nothing runs is a copy; a root something runs is
    a live path a publication can travel. Only processes whose command line
    names the plant package are read, and only their ``PYTHONPATH`` is taken.
    """
    live: dict[Path, int] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return live
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            environ = (entry / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        if PACKAGE not in cmdline:
            continue
        for item in environ:
            if not item.startswith((PYTHONPATH_ENV + "=").encode()):
                continue
            value = item.split(b"=", 1)[1].decode("utf-8", "replace")
            for part in value.split(":"):
                if not part:
                    continue
                root = Path(part)
                if _is_root(root):
                    live[root] = live.get(root, 0) + 1
    return live


def candidate_roots(home: Path) -> dict[Path, str]:
    """Every root on this site that can carry a publication, with its reason.

    The set is the roots the plant itself names — the pin, the CLI entry point,
    the declared off-pin roots and the roots its own processes run. Snapshot
    copies under ``artifacts/`` are not in it: a copy that nothing runs and
    that no record names cannot publish, so calling it a root would report
    the plant's history as its present.
    """
    roots: dict[Path, str] = {}
    pin = pinned_root(home)
    if pin is not None:
        roots[pin] = "the pin"
    entry = entry_point_root(home)
    if entry is not None:
        roots.setdefault(entry[0], "the CLI entry point")
    for root, unit in declared_roots(home).items():
        roots.setdefault(root, "declared by %s" % unit)
    live = live_roots()
    for root in live:
        roots.setdefault(root, "a running process")
    return roots


def _disposable_home() -> Path:
    """A throwaway site home the probe may write into.

    The probe must never publish into the plant's own home: the plant's
    binding rules treat a second conversation tape as a defect, and a probe
    publication would bind evidence the plant did not intend to bind. The
    probe home lives in the system temporary directory, outside the plant.
    """
    home = Path(tempfile.mkdtemp(prefix="clause-probe-"))
    for name in ("artifacts", "minds", "top-pains", "charters", "walls", "records", "logs"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "chat.log").write_text("", encoding="utf-8")
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall"}), encoding="utf-8")
    launcher = home / "minds" / "inference-research"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(launcher, 0o755)
    (home / "charters" / "inference-research.md").write_text("# probe\n", encoding="utf-8")
    return home


def _publish(home: Path, root: Path, evidence: Path, prose: Path) -> subprocess.CompletedProcess:
    """Publish one outcome through ``root`` into the disposable home."""
    env = dict(os.environ)
    env[PYTHONPATH_ENV] = str(root)
    env.pop("MISHE_SEED_HOME", None)
    return subprocess.run(
        [sys.executable, "-m", PACKAGE, "--home", str(home), "wall", "outcome",
         "--owner", "inference-research", "--kind", "accepted",
         "--file", str(prose), "--evidence", str(evidence)],
        capture_output=True, text=True, env=env, cwd=str(home), timeout=120)


def _loaded_module(root: Path) -> str:
    """The module path the plant package loads from ``root``, or an error."""
    env = dict(os.environ)
    env[PYTHONPATH_ENV] = str(root)
    env.pop("MISHE_SEED_HOME", None)
    result = subprocess.run(
        [sys.executable, "-c", "import %s; print(%s.__file__)" % (PACKAGE, PACKAGE)],
        capture_output=True, text=True, env=env, cwd=str(root), timeout=120)
    if result.returncode != 0:
        return "unresolved: " + (result.stderr or "").strip()[-200:]
    return result.stdout.strip()


def _replace(path: Path) -> None:
    """Replace the file at ``path`` the way the plant's own writers do.

    Every writer in this plant that saves a file writes a temporary sibling and
    renames it over the target, so the mode the target had does not carry
    over. Doing exactly that is what makes the probe a measurement rather
    than an assertion.
    """
    temporary = path.parent / ("." + path.name + ".probe")
    temporary.write_bytes(b"# replaced by the probe\n")
    os.replace(temporary, path)


def probe_root(root: Path) -> dict:
    """Whether a file the clause made read-only can be replaced anyway.

    The probe publishes a bound artifact through ``root``, checks the mode the
    clause left it in, then replaces it the way the plant's writers do and
    checks whether the mode and the bytes survived.
    """
    probe_home = _disposable_home()
    try:
        evidence = probe_home / "artifacts" / "clause-probe.md"
        evidence.write_text("# bound artifact\n\nthe bytes the outcome binds\n", encoding="utf-8")
        prose = probe_home / "prose.md"
        prose.write_text("probe publication for the write-once clause\n", encoding="utf-8")
        before = stat.S_IMODE(evidence.stat().st_mode)
        digest_before = hashlib.sha256(evidence.read_bytes()).hexdigest()
        result = _publish(probe_home, root, evidence, prose)
        if result.returncode != 0:
            return {"decided": False,
                    "reason": (result.stderr or result.stdout or "").strip()[-300:]}
        after_publish = stat.S_IMODE(evidence.stat().st_mode)
        _replace(evidence)
        after_replace = stat.S_IMODE(evidence.stat().st_mode)
        digest_after = hashlib.sha256(evidence.read_bytes()).hexdigest()
        return {
            "decided": True,
            "mode_before": before,
            "mode_after_publish": after_publish,
            "mode_after_replace": after_replace,
            "clause_applied": not (after_publish & 0o222),
            "read_only_survived": not (after_replace & 0o222),
            "bytes_survived": digest_before == digest_after,
        }
    finally:
        shutil.rmtree(probe_home, ignore_errors=True)


def clause_time(repo: Path) -> float | None:
    """When the write-once clause landed, as a POSIX timestamp.

    The clause's date is taken from the commit that introduced it rather than
    from a literal in this file, so the measurement does not drift as the
    tree moves.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ct", CLAUSE_COMMIT],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def standing_loss(home: Path, since: float | None) -> dict:
    """The bound rows whose bytes no longer match the digest they recorded.

    The rows are read from the published record store, which is the plant's
    own record of every binding it has made. The two populations are reported
    apart, because they have different causes: a row that was published
    writable never had the clause applied to it, while a row that is read-only
    now and yet no longer matches cannot have been edited in place — the
    clause was applied and then defeated.
    """
    directory = home / RECORDS_DIR
    if not directory.is_dir():
        return {"decided": False, "reason": "the record store is not readable"}
    total = 0
    lost_writable = 0
    lost_readonly = 0
    missing = 0
    for record in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "wall-outcome":
            continue
        body = payload.get("payload")
        if not isinstance(body, dict):
            continue
        evidence = body.get("evidence")
        if not isinstance(evidence, dict):
            continue
        path = evidence.get("path")
        digest = evidence.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            continue
        if since is not None and record.stat().st_mtime < since:
            continue
        target = Path(path)
        if not target.is_absolute():
            target = home / target
        try:
            data = target.read_bytes()
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            missing += 1
            continue
        total += 1
        if hashlib.sha256(data).hexdigest() == digest:
            continue
        if mode & 0o222:
            lost_writable += 1
        else:
            lost_readonly += 1
    return {"decided": True, "total": total, "lost_writable": lost_writable,
            "lost_readonly": lost_readonly, "missing": missing}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    args = parser.parse_args(argv)
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()

    try:
        roots = candidate_roots(home)
        if not roots:
            raise Undecidable("no root that can carry a publication was found under %s" % home)
        live = live_roots()
        states = {root: carries_clause(root) for root in roots}
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    print("site home : %s" % home)
    print("roots     : %d root(s) that can carry a publication" % len(roots))
    print("")
    absent = []
    for root in sorted(roots, key=lambda p: str(p)):
        state = "carries the clause" if states[root] else "DOES NOT CARRY the clause"
        print("  %-9s live=%-3d %-28s %s" % (state, live.get(root, 0), roots[root], root))
        if not states[root]:
            absent.append(root)

    print("")
    print("clause time: commit %s (%s)" % (CLAUSE_COMMIT, _clause_stamp(repo)))
    print("read-only : commit %s" % READ_ONLY_COMMIT)
    print("")

    failed = False
    if absent:
        failed = True
        print("VERDICT: %d root(s) that can carry a publication do not carry the write-once "
              "clause — every outcome published through %s leaves its bound evidence writable"
              % (len(absent), "them" if len(absent) > 1 else "it"))
        for root in sorted(absent, key=lambda p: str(p)):
            print("          %s (%s)" % (root, roots[root]))
    else:
        print("verdict   : every root that can carry a publication carries the clause")

    print("")
    print("probe     : does a bound file the clause made read-only survive a replacement?")
    probed = 0
    for root in sorted(roots, key=lambda p: (-live.get(p, 0), str(p))):
        if not states[root]:
            continue
        try:
            result = probe_root(root)
        except (OSError, subprocess.SubprocessError) as exc:
            print("          %s — the probe could not run: %s" % (root, type(exc).__name__))
            continue
        if not result.get("decided"):
            print("          %s — the probe could not publish: %s" % (root, result.get("reason")))
            continue
        probed += 1
        print("          %s" % root)
        print("            module loaded  : %s" % _loaded_module(root))
        print("            after publish  : %s" % oct(result["mode_after_publish"]))
        print("            after replace  : %s" % oct(result["mode_after_replace"]))
        print("            bytes survived : %s" % ("yes" if result["bytes_survived"] else "no"))
        if not result["read_only_survived"] or not result["bytes_survived"]:
            failed = True
            print("            the clause did not hold: the plant's own writers replace the "
                  "path, so the mode the clause applied is gone")
        if probed >= 3:
            break

    if probed == 0:
        print("UNKNOWN — no root that carries the clause could be probed")
        return 3

    print("")
    since = clause_time(repo)
    if since is None:
        print("standing loss: UNKNOWN — the clause commit %s could not be read from the "
              "reviewed repository" % CLAUSE_COMMIT)
    else:
        loss = standing_loss(home, since)
        if not loss.get("decided"):
            print("standing loss: UNKNOWN — %s" % loss.get("reason"))
        else:
            print("standing loss since the clause (%d bound row(s) read):" % loss["total"])
            print("  lost, file writable now (no clause was applied) : %d" % loss["lost_writable"])
            print("  lost, file read-only now (the clause was defeated): %d" % loss["lost_readonly"])
            print("  bound file missing                              : %d" % loss["missing"])

    if failed:
        return 2
    print("")
    print("VERDICT: the write-once clause is present and effective on this site")
    return 0


def _clause_stamp(repo: Path) -> str:
    """The clause commit's date, as text, or a note that it cannot be read."""
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%cI", CLAUSE_COMMIT],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return "unreadable"
    return result.stdout.strip() or "unreadable"


if __name__ == "__main__":
    sys.exit(main())
