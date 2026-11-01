#!/usr/bin/env python3
"""Detector: a shipped page's stated scope for the idle figure must be the
scope the committed tree implements.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a
page names exists.  ``checks/shipped-book-promises.py`` asks whether the page
a promise points at *contains* what the promise names.  ``checks/shipped-book-
evidence.py`` asks whether the *rule* a page states about a relative evidence
path is the rule the committed tree applies.

None of them asks what a figure on a pane *measures*.  A page can describe a
number that the plant prints on every pane and leave its scope unstated or
wrong, and every existing gate passes it: the page is reachable, its paths
exist, and it states no rule about a relative evidence path.

Measured on this plant on 2026-11-01 (Docs wake 152920): every pane leads
with a ``HEADLINE`` line carrying ``mind ok idle <N>s``.  The book described
the surrounding line -- "the activity line shows seconds since the latest mind
message, wall edit or settled turn" (``docs/wall-coordination.md``) -- without
saying whose activity that is.  It is **plant-wide**: ``activity.observe()``
takes the maximum over *every* monitored role's wall and over the shared tape
(``src/mishe_tauftauf/activity.py``), and ``observations.headline()`` takes no
role argument at all (``src/mishe_tauftauf/observations.py``), so the same
number is printed on every pane.  A reader asking "is this mind idle?" reads
a number that a neighbour's activity reset.

The rule this check enforces:

* a shipped page may state the **scope** of the idle figure -- whether it
  measures one mind or the plant;
* when it does, the statement must agree with the scope the **committed tree**
  implements.

The scope is read by **executing** it: the check materialises the committed
bytes, plants a site against them, gives two monitored roles different activity
ages, and reads the figure the tree renders on the quiet role's own pane.  If
that figure is reset by the *other* role's activity, the scope is plant-wide;
if it is near the quiet role's own hour of silence, the scope is per-mind.

Three things this needs, each measured rather than assumed (Docs wake
152920):

* the figure is read off the role's **frame**, not off ``headline()``.
  ``headline()`` takes no role argument, so it would report plant-wide for any
  tree and the check could not tell a per-mind implementation from a
  plant-wide one;
* the planted site is bound to **itself** while the frame runs.  A mind
  process is bound to the site its supervisor named, and the check inherits
  the environment of whoever ran it -- without this the frame refuses the
  planted site as a foreign home;
* the site's "plant start" candidate is moved back past the measured ages.
  A freshly planted site stamps it at the present moment, which would be the
  newest action in the plant and would make every figure small.

Scope and limits, named:

* Only pages that **state a scope for the idle figure** are read.  A page that
  says nothing about it is not a finding; the plant has many pages and most of
  them are about other things.
* The stated scope is read from a small, explicit vocabulary.  A page that
  describes the scope in prose the vocabulary does not cover is not a finding,
  and that is a known limit rather than a cleared question.
* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current.
* The check runs the real code, so it is bounded by what a site needs to
  produce an activity reading: a tape, walls and a coordination mode.  When the
  planted site cannot reach the reading at all, the reading is UNKNOWN (exit 3),
  never a pass.
* The check compares the *scope*, not the wording.  A page that describes the
  scope correctly in its own words still passes, because the check looks for
  the scope the page states, not for a sentence it expects.

Exit status:
  0  every stated idle scope matches the committed tree
  2  a shipped page states a scope the committed tree does not implement
  3  the question cannot be decided: no tree, no git, or the tree cannot be run
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
import time
from datetime import datetime, timezone
from pathlib import Path

GIT = "/usr/bin/git"

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# The phrases a page may use to state the scope of the idle figure.
# Each entry is (marker, scope) where scope is "plant-wide" or "per-mind".
STATEMENTS = (
    (re.compile(r"\bplant[- ]wide\b", re.I), "plant-wide"),
    (re.compile(r"\bacross (?:all|every) (?:mind|minds|pane|panes)\b", re.I), "plant-wide"),
    (re.compile(r"\bnot per[- ]mind\b", re.I), "plant-wide"),
    (re.compile(r"\bper[- ]mind\b", re.I), "per-mind"),
    (re.compile(r"\beach mind'?s own\b", re.I), "per-mind"),
    (re.compile(r"\bthat mind'?s own\b", re.I), "per-mind"),
)

# The rule this check reads: the scope of the idle figure.
#
# The scope is narrow on purpose.  A page talks about idleness in many places
# -- a settled mind awaiting an idle boundary, an idle prompt a readiness gate
# recognises -- and most of those are about a different thing entirely.  So a
# statement is read only where the text is about the *figure on the pane*: it
# names the figure (the idle seconds, the activity line, the headline's idle
# field), and it states a scope.
#
# The figure markers are deliberately specific.  A bare "plant-wide" or
# "headline" is not enough: the plant shares locks and panes plant-wide, and
# a page may name the headline for an unrelated reason.  What ties a
# statement to the figure is the *figure itself* -- an idle count in seconds,
# the activity line, or the mind-state field of the headline.
FIGURE = re.compile(
    r"(idle\s+(?:figure|seconds|count|number|field|value|age)|"
    r"`idle\b|activity line|"
    r"mind\s+(?:ok|silent|ended|unknown)\b|"
    r"last mind action)",
    re.I)

# A phrase that ties the statement to the figure rather than to idleness in
# general.  Without it, "a settled mind awaits an idle boundary" would be read
# as a claim about the figure's scope, and it is not.
SCOPE_MARKER = re.compile(
    r"(plant[- ]wide|per[- ]mind|each mind'?s own|that mind'?s own|"
    r"across (?:all|every) (?:mind|minds|pane|panes)|"
    r"any monitored mind|every monitored role|all monitored|"
    r"the same (?:number|figure|reading|value)|one reading serves)",
    re.I)

BLOCK = re.compile(r"\n\s*\n")
FENCE = re.compile(r"```.*?```", re.DOTALL)
BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s")
SENTENCE = re.compile(r"(?<=[.;:])\s+|\n")


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-idle-scope-")
    archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                             capture_output=True)
    if archive.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, archive.stderr.decode("utf-8", "replace").strip()
    unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                            capture_output=True)
    if unpack.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, unpack.stderr.decode("utf-8", "replace").strip()
    return Path(tmp), ""


def _package_dir(tree: Path) -> Path | None:
    """The plant package inside a tree, if the tree carries one."""
    for candidate in (tree / "src" / "mishe_tauftauf", tree / "mishe_tauftauf"):
        if candidate.is_dir():
            return candidate
    return None


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _logical_units(block: str) -> list[str]:
    """The rules a block states, one per bullet or paragraph line.

    Markdown hard-wraps a paragraph, so the lines of one rule are several
    physical lines.  A bullet starts a new rule; the lines that follow it,
    indented or not, belong to that rule.
    """
    units: list[str] = []
    current: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if BULLET.match(line) and current:
            units.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        units.append(" ".join(current))
    return units


def _stated_scopes(page: Path) -> list[dict]:
    """The scopes a page states for the idle figure.

    A fenced block is an example, not a claim about the rule, so it is not
    read.  The unit of reading is the **rule**: a bullet list states several
    rules in one block, and they have different answers.
    """
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    prose = FENCE.sub("", text)
    found: list[dict] = []
    for block in BLOCK.split(prose):
        if not block.strip():
            continue
        for unit in _logical_units(block):
            unit = unit.strip().lstrip("-*").strip()
            if not unit:
                continue
            scope = _scope_of_figure(unit)
            if scope is None:
                continue
            found.append({"scope": scope, "claim": " ".join(unit.split())[:300]})
    return found


def _scope_of_figure(unit: str) -> str | None:
    """The scope a rule states for the idle figure.

    A rule can state both scopes in one unit -- "that age is plant-wide, not
    per-mind" names the figure's scope twice, once as the true scope and once
    as the false one.  So the scope is read from the sentence that **states**
    it, and the leading marker wins: a rule that says "plant-wide, not
    per-mind" states plant-wide.

    The figure must be named in the *same sentence* as the scope.  A page
    that says "the watcher shares the plant-wide seed lock" states a scope,
    but not one about the idle figure; requiring the figure marker in the
    same sentence is what keeps that from reading as a claim about it.
    """
    for sentence in SENTENCE.split(unit):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not FIGURE.search(sentence):
            continue
        # "plant-wide, not per-mind" -- the true scope comes first.
        for pattern, scope in STATEMENTS:
            match = pattern.search(sentence)
            if match:
                return scope
    return None


def _plant_site(tree: Path, work: Path) -> Path:
    """Plant a site against the committed tree and return its home."""
    site_root = work / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q", str(site_root)], capture_output=True)
    home = site_root / ".mishe-tauftauf"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["MISHE_SEED_HOME"] = str(home)
    result = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                             "init"], env=env, capture_output=True, cwd=str(work))
    if result.returncode:
        raise ValueError("the committed tree could not be planted: "
                         + (result.stderr.decode("utf-8", "replace").strip()
                            or result.stdout.decode("utf-8", "replace").strip()))
    for name in ("walls", "artifacts", "records"):
        (home / name).mkdir(exist_ok=True)
    return home


def _observe(tree: Path, home: Path) -> dict:
    """Read the scope the tree implements, by exercising it.

    The figure is read the way a reader reads it: the watcher writes its
    reading, and the role's **frame** renders the headline that carries it.
    Two monitored roles are given different activity ages -- one quiet for an
    hour, the other acting now -- and the figure that appears on the *quiet*
    role's pane is read.

    If the figure on the quiet role's pane is small, it was reset by the
    other role's activity and the scope is plant-wide.  If it is near the
    quiet role's own hour of silence, the scope is per-mind.
    """
    sys.path.insert(0, str(tree / "src"))
    try:
        from mishe_tauftauf import activity, observations  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    quiet, active = "docs", "health"
    now = time.time()
    for role in (quiet, active):
        path = home / "walls" / f"{role}.md"
        path.write_text(f"# {role}\n", encoding="utf-8")
        os.utime(path, (now - 3600, now - 3600))

    # The activity reading also counts "plant start" as an action, taken from
    # the site's coordination mode.  A freshly planted site stamps that at the
    # present moment, which would be the newest action in the plant and would
    # make every figure small -- the reading would then be plant-wide for
    # *any* implementation, and the check could not tell a per-mind
    # implementation from a plant-wide one.  So the start is moved back past
    # the measured ages, and the figure is then decided by the walls alone.
    mode_path = home / "coordination-mode.json"
    try:
        mode = json.loads(mode_path.read_text())
    except (OSError, ValueError):
        mode = {}
    if isinstance(mode, dict):
        mode["started"] = datetime.fromtimestamp(now - 7200, timezone.utc).isoformat()
        mode_path.write_text(json.dumps(mode, indent=2) + "\n", encoding="utf-8")

    def render(role: str) -> tuple[float | None, str, str]:
        """Run the watcher, then read the idle figure off the role's frame.

        The frame runs under the planted site's own binding.  A mind process
        is bound to the site its supervisor named (``MISHE_SEED_HOME``), and
        the check inherits the environment of whoever ran it -- so without
        this the frame refuses the planted site as a foreign home and the
        figure cannot be read at all.
        """
        bound = os.environ.get("MISHE_SEED_HOME")
        os.environ["MISHE_SEED_HOME"] = str(home)
        try:
            activity.check(home)
            frame = observations.compose_frame(home, role, timeout=30.0)
        finally:
            if bound is None:
                os.environ.pop("MISHE_SEED_HOME", None)
            else:
                os.environ["MISHE_SEED_HOME"] = bound
        headline = frame.body.splitlines()[0] if frame.body else ""
        match = re.search(r"\bidle\s+([0-9]+(?:\.[0-9]+)?)s", headline)
        reading = json.loads((home / "checks" / "silence-watch.json").read_text())
        if not match:
            return None, reading.get("evidence", ""), headline.strip()
        return float(match.group(1)), reading.get("evidence", ""), headline.strip()

    # The quiet role's own wall is an hour old; the other role acts now.
    os.utime(home / "walls" / f"{active}.md", (now, now))
    time.sleep(1.1)
    first, first_evidence, first_headline = render(quiet)

    if first is None:
        raise ValueError("the committed tree rendered no idle figure")

    # Now let the quiet role act as well, and read the pane of the role that
    # is now the quiet one.  The figure must follow the plant's newest
    # activity either way.
    now = time.time()
    os.utime(home / "walls" / f"{active}.md", (now - 3600, now - 3600))
    os.utime(home / "walls" / f"{quiet}.md", (now, now))
    time.sleep(1.1)
    second, second_evidence, second_headline = render(active)

    # The quiet role's own wall is an hour old, so a per-mind figure for its
    # pane would be near 3600.  A plant-wide figure is reset by the other
    # role and stays small.
    plant_wide = first < 60.0 and (second is None or second < 60.0)
    return {"scope": "plant-wide" if plant_wide else "per-mind",
            "first_idle_seconds": round(first, 1),
            "first_evidence": first_evidence,
            "first_headline": first_headline,
            "second_idle_seconds": None if second is None else round(second, 1),
            "second_evidence": second_evidence,
            "second_headline": second_headline}


def audit(tree: Path) -> dict:
    """Find pages that state a scope the committed tree does not implement."""
    if _package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    work = Path(tempfile.mkdtemp(prefix="shipped-book-idle-scope-run-"))
    try:
        home = _plant_site(tree, work)
        reading = _observe(tree, home)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        for statement in _stated_scopes(page):
            checked += 1
            if statement["scope"] == reading["scope"]:
                continue
            findings.append({
                "file": source,
                "stated": statement["scope"],
                "observed": reading["scope"],
                "claim": statement["claim"],
            })
    return {"book_pages": len(pages), "statements": checked,
            "reading": reading, "findings": findings}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True,
                        help="site home (kept for the observer-checks runner contract)")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are tested")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    args = parser.parse_args(argv)

    tmp: str | None = None
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

    try:
        result = audit(tree)
        reading = result["reading"]
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} "
              f"statements={result['statements']} "
              f"observed scope={reading['scope']} "
              f"(first idle={reading['first_idle_seconds']}s "
              f"evidence={reading['first_evidence']!r}; "
              f"second idle={reading['second_idle_seconds']}s "
              f"evidence={reading['second_evidence']!r})")
        for item in result["findings"]:
            print(f"  STALE IDLE SCOPE  {item['file']} states the idle figure is "
                  f"{item['stated']}, but the committed tree measures it {item['observed']}")
            print(f"    {item['claim']}")
        if not result["findings"]:
            print("OK every stated idle scope matches the committed tree")
            return 0
        print("FAIL a shipped page states an idle scope the committed tree does "
              "not implement")
        return 2
    except (OSError, ValueError, SyntaxError, UnicodeError, ImportError) as exc:
        print(f"UNKNOWN — shipped-book idle-scope check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
