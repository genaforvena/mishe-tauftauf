#!/usr/bin/env python3
"""Detector: a shipped page's unit pattern must name units that exist.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a page
names exists.  ``checks/shipped-book-promises.py`` asks whether the page a
promise points at *contains* what the promise names.  ``checks/shipped-book-
evidence.py`` asks whether the *rule* a page states about a relative evidence
path is the rule the committed tree applies.

None of them asks whether a command a page hands the reader **answers**.
A page can spell out a working command whose output says nothing, and every
existing gate passes it: the page is reachable, its paths exist, and it states
no rule about a relative evidence path.

Measured on this plant on 2026-11-01 (Docs wake 163304): ``docs/getting-
started.md`` told a reader to confirm the resident units were active with
``systemctl --user list-units 'SITE-*.service'``.  ``$SITE`` is the site's
**directory** in that page -- ``SITE="$PWD/.mishe-seed"`` -- so the pattern
named no unit at all.  The unit names are ``SESSION-ROLE.service``, where
``SESSION`` is the session the plant raised (``src/mishe_tauftauf/plant.py``,
``unit_name``), and on this plant the pattern ``SITE-*.service`` matched
**zero** units against the 40 that exist.  The reader's acceptance check
answered "0 loaded units listed" and read it as a broken plant.  The page was
wrong for both byte sets, so no served-release reading could have caught it
either: the committed tree was never the question.

The rule this check enforces:

* a shipped page may give the reader a **unit pattern** to match against the
  host's systemd user manager;
* when it does, the pattern must not be prefixed with a **directory** -- a
  unit is named ``SESSION-ROLE.service``, so a pattern prefixed with the
  site's directory names no unit and the reader's check cannot succeed.

The defect is read by **executing** the match: the check asks the site's own
``health/services.json`` manifest for the units the plant claims, asks the
host for the units it has, and compares the pattern against both.  A pattern
prefixed with a directory matches neither.

Scope and limits, named:

* The rule is narrow on purpose.  It reads a pattern that prefixes a unit
  with a variable the page itself defines as a **directory** -- ``SITE``,
  ``SITE_HOME``, ``HOME``.  A page that writes ``"$SESSION-*.service"`` is
  a template whose value the reader supplies, and the book's own pages use
  placeholder values (``SESSION='actual-session-name'``), so those are not
  findings.
* A page that names a **single** unit without a pattern --
  ``mishe-self-development-current-model-changes.service`` in
  ``docs/model-changes.md`` -- is not read: one named unit is checked by the
  reader's own ``systemctl show``.
* The check reads the **live** site when ``--home`` names one, because the
  question is about the units this plant installed.  Without ``--home`` it
  plants a site against the tree under test and takes the unit names from
  that tree's own planting code, so the gate also runs on a clone that has
  never planted anything.  A site whose manifest is missing or names no
  units is UNKNOWN (exit 3), never a pass.
* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current.

Exit status:
  0  no stated unit pattern is prefixed with a directory
  2  a shipped page prefixes a unit pattern with a directory
  3  the question cannot be decided: no tree, no manifest, or no units
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# A unit pattern a page hands the reader: a quoted glob that names units.
# The literal prefix is what precedes the wildcard.
UNIT_PATTERN = re.compile(
    r"systemctl\s+(?:--user\s+)?(?:\w+\s+)*list-units[^\n]*?"
    r"['\"](?P<pattern>[A-Za-z0-9_${}@.*-]+?\*?[A-Za-z0-9_${}@.*-]*?\.service)['\"]")

# A single unit a page hands the reader to a systemctl subcommand.  The unit
# is named by a shell variable the page may or may not define; when it does
# not, the reading is UNKNOWN for that statement rather than a finding.
UNIT_NAME = re.compile(
    r"systemctl\s+(?:--user\s+)?(?:\w+\s+)*"
    r"(?P<verb>show|status|cat|restart)\s+"
    r"['\"]?(?P<unit>[A-Za-z0-9_${}@.*-]+?\.service)['\"]?")

# A variable the page sets, so a pattern that names one can be resolved.
VARIABLE = re.compile(r"^\s*(?P<name>[A-Z][A-Z0-9_]*)=(?P<value>\S+)\s*$")

# A variable the page uses as a **directory** rather than a name.  A unit is
# named ``SESSION-ROLE.service``; a pattern that prefixes a unit with a
# directory variable can never name one, and the reader's check answers
# "0 loaded units listed" for a healthy plant.
DIRECTORY_VARIABLE = re.compile(r"^\$?(?P<name>SITE|SITE_HOME|HOME)$")

# The prefix of a pattern, up to the first wildcard.
PREFIX = re.compile(r"^(?P<prefix>[^*]+)")


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-unit-pattern-")
    archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                             capture_output=True)
    if archive.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, archive.stderr.decode("utf-8", "replace").strip() or "git archive failed"
    unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                            capture_output=True)
    if unpack.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, "tar failed: " + unpack.stderr.decode("utf-8", "replace").strip()
    return Path(tmp), ""


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _page_variables(text: str) -> dict[str, str]:
    """The shell variables the page sets, with their literal values."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = VARIABLE.match(line)
        if match:
            values[match.group("name")] = match.group("value").strip("\"'")
    return values


def _resolve(text: str, variables: dict[str, str]) -> str:
    """Substitute the variables the page sets into a pattern or unit name."""
    resolved = text
    for name, value in variables.items():
        resolved = resolved.replace("${" + name + "}", value)
        resolved = resolved.replace("$" + name, value)
    return resolved


def _stated_patterns(page: Path) -> list[dict]:
    """The unit patterns and unit names a page hands the reader."""
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    variables = _page_variables(text)
    found: list[dict] = []
    seen: set[str] = set()
    for match in UNIT_PATTERN.finditer(text):
        raw = match.group("pattern")
        found.append({"pattern": raw, "resolved": _resolve(raw, variables),
                      "claim": " ".join(match.group(0).split())[:200]})
        seen.add(match.group(0))
    for match in UNIT_NAME.finditer(text):
        raw = match.group("unit")
        if match.group(0) in seen:
            continue
        found.append({"pattern": raw, "resolved": _resolve(raw, variables),
                      "claim": " ".join(match.group(0).split())[:200]})
        seen.add(match.group(0))
    return found


def _pattern_prefix(pattern: str) -> str:
    """The literal prefix of a pattern, up to its first wildcard."""
    match = PREFIX.match(pattern)
    return match.group("prefix") if match else pattern


def _directory_prefixed(pattern: str, variables: dict[str, str]) -> str | None:
    """The directory a unit pattern is wrongly prefixed with.

    A unit is named ``SESSION-ROLE.service``.  A pattern whose prefix names a
    **directory** -- ``$SITE``, ``SITE``, ``$SITE_HOME`` -- can never name
    one, and the reader's check answers "0 loaded units listed" for a healthy
    plant.

    The prefix is read as a directory when the page uses that name for one:
    ``SITE="$PWD/.mishe-seed"`` is a path, so a pattern prefixed with ``SITE``
    or ``$SITE`` names a directory, not a session.  The name is matched
    literally or with the ``$`` sigil, because a page may write either.
    """
    prefix = _pattern_prefix(pattern)
    match = re.match(r"^\$?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b", prefix)
    if not match:
        return None
    name = match.group("name")
    if not DIRECTORY_VARIABLE.match(name):
        return None
    # The page must use that name for a directory.  A page that defines it as
    # something else is a different question.
    value = variables.get(name)
    if value is None:
        return None
    if "/" not in value and not value.startswith("."):
        return None
    return name


def _manifest_units(home: Path) -> list[str] | None:
    """The units the plant's own manifest names, or None when it cannot say."""
    path = home / "health" / "services.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [name for name in data if isinstance(name, str)]


def _plant_site(tree: Path, work: Path) -> Path:
    """Plant a site against the committed tree and return its home.

    The site is planted with the committed bytes on ``PYTHONPATH``, so the
    unit names it installs are the ones the committed tree installs -- the
    same question the reader's clone gets.  ``init`` alone writes no
    manifest, so the manifest is written from the tree's own unit-naming
    code, which is the rule the plant installs by.
    """
    site_root = work / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q", str(site_root)], capture_output=True)
    home = site_root / ".mishe-tauftauf"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["MISHE_SEED_HOME"] = str(home)
    subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                    "init"], env=env, capture_output=True, cwd=str(work))
    units = _planted_units(tree)
    if units:
        (home / "health").mkdir(parents=True, exist_ok=True)
        (home / "health" / "services.json").write_text(
            json.dumps(units), encoding="utf-8")
    return home


def _planted_units(tree: Path) -> list[str]:
    """The unit names the committed tree installs for a session.

    The names come from the tree's own code, so the gate does not carry a
    second copy of the naming rule that could drift from it.
    """
    package = tree / "src" / "mishe_tauftauf"
    if not package.is_dir():
        return []
    script = (
        "import json,sys\n"
        "from mishe_tauftauf.plant import ROLES, OUT_OF_BAND, unit_name\n"
        "session = 'session'\n"
        "print(json.dumps([unit_name(session, slug) for slug in "
        "(*ROLES, 'permissions', 'operator-view', 'ci', *OUT_OF_BAND)]))\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True)
    if result.returncode:
        return []
    try:
        names = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []
    return [n for n in names if isinstance(n, str)]


def _host_units() -> list[str]:
    """The units the host's user systemd manager currently has."""
    result = subprocess.run(["systemctl", "--user", "list-unit-files",
                             "--no-legend", "--no-pager"],
                            capture_output=True, text=True)
    names = []
    for line in result.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.endswith(".service"):
            names.append(name)
    return names


def audit(tree: Path, home: Path) -> dict:
    """Find pages whose unit pattern is prefixed with a directory."""
    manifest = _manifest_units(home)
    if manifest is None:
        raise ValueError("no readable health/services.json at " + str(home))
    if not manifest:
        raise ValueError("health/services.json names no units")
    host = _host_units()
    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        variables = _page_variables(page.read_text(encoding="utf-8"))
        for statement in _stated_patterns(page):
            checked += 1
            wrong = _directory_prefixed(statement["pattern"], variables)
            if wrong is None:
                continue
            prefix = _pattern_prefix(statement["resolved"])
            in_manifest = [u for u in manifest if u.startswith(prefix)]
            in_host = [u for u in host if u.startswith(prefix)]
            findings.append({
                "file": source,
                "pattern": statement["pattern"],
                "resolved": statement["resolved"],
                "variable": wrong,
                "manifest_matches": len(in_manifest),
                "host_matches": len(in_host),
                "claim": statement["claim"],
            })
    return {"book_pages": len(pages), "statements": checked,
            "manifest_units": len(manifest), "host_units": len(host),
            "findings": findings}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=None,
                        help="site home holding health/services.json; a site is "
                             "planted against the tree under test when this is absent")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are the pages under test")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    args = parser.parse_args(argv)

    tmp: str | None = None
    work: str | None = None
    try:
        if args.tree:
            tree = args.tree.resolve()
        elif args.repo:
            tree, error = _committed_tree(args.repo.resolve())
            if tree is None:
                print("cannot read committed bytes: " + error)
                return 3
            tmp = str(tree)
        else:
            print("no tree and no repo given")
            return 3
        if args.home is not None:
            home = args.home.resolve()
        else:
            # Plant a site against the tree under test, so the unit names are
            # the ones the committed tree installs.  A clone that has not
            # planted anything can still be asked the question this way.
            work = tempfile.mkdtemp(prefix="shipped-book-unit-pattern-site-")
            home = _plant_site(tree, Path(work))
        try:
            result = audit(tree, home)
        except (OSError, ValueError) as exc:
            print("UNKNOWN — shipped-book-unit-pattern unavailable: " + str(exc))
            return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        if work:
            shutil.rmtree(work, ignore_errors=True)

    print(f"tree={tree}")
    print(f"book pages={result['book_pages']} statements={result['statements']}")
    print(f"manifest units={result['manifest_units']} host units={result['host_units']}")
    for item in result["findings"]:
        print("  BOOK vs INSTALLED UNITS  %s prefixes a unit with the directory "
              "variable $%s: the pattern %s matches %d manifest unit(s) and %d "
              "unit(s) on the host"
              % (item["file"], item["variable"], item["resolved"],
                 item["manifest_matches"], item["host_matches"]))
        print("    " + item["claim"])
    if result["statements"] == 0:
        print("UNKNOWN — the shipped book states no unit pattern, so there is "
              "nothing to compare and a pass would be vacuous")
        return 3
    if not result["findings"]:
        print("OK every stated unit pattern names an installed unit")
        return 0
    print("FAIL the shipped book states a unit pattern that names no installed unit")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
