#!/usr/bin/env python3
"""Detector: a shipped page's site-placement rule must be the one the clone enforces.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a
page names exists in the clone.  ``checks/shipped-book-promises.py`` asks
whether the page a promise points at *contains* what the promise names.
``checks/shipped-book-runtimes.py`` asks whether a runtime a page describes is
reachable from the committed tree.  ``checks/shipped-book-evidence.py`` asks
whether the evidence-resolution rule a page states is the rule the committed
code applies.

None of them asks *where the plant's own state may live*.  A page can tell
the reader that the site sits outside the repository, and every other gate
passes it: no path is named, no link is broken, no runtime is missing.  The
reader then places their site somewhere the clone refuses to plant, and the
first command they run fails.

Measured on this plant on 2026-11-01 (Docs wake 152920): the shipped
``docs/mesh.md`` closed its opening with "Each plant keeps its local state
outside the shared source repository."  The committed tree enforces the
opposite.  ``seed._require_worktree`` refuses a site that is not **directly
inside a Git worktree** and refuses a site that *is* a repository root, and
``plant.py`` calls it on the real planting path, so a reader who follows the
page cannot plant at all.  Five other shipped pages state the enforced rule
(``README.md``, ``docs/getting-started.md``, ``docs/operating.md``,
``docs/how-it-works.md``); this one contradicted them.

The rule this check enforces:

* a shipped page may state **where the site home lies**;
* when it does, the statement must agree with the placement the **committed
  tree** enforces -- not with any release, and not with the working tree.

The rule is read from the committed tree by **executing** it: the check
materialises the committed bytes and runs the real ``init`` against three
placements a reader can choose:

* **inside** -- the site is a directory directly inside a Git worktree;
* **outside** -- the site is a directory outside every Git worktree;
* **nested** -- the site is a directory inside a subdirectory of a worktree.

The observed outcomes are what the page must match.  A page that says the
outside form is refused must not also be the page a reader follows when the
outside form is accepted, and the reverse.

Scope and limits, named:

* Only pages that **state a placement** are read.  A page that says nothing
  about where the site lives is not a finding; the plant has many pages and
  most of them are about other things.
* The stated placement is read from a small, explicit vocabulary of phrases
  ("inside the repository", "outside the repository", "inside the
  worktree", ...).  A page that describes the rule in prose the vocabulary
  does not cover is not a finding, and that is a known limit rather than a
  cleared question.
* The phrase must sit in a sentence whose subject is something *other than*
  the phrase's own nouns.  The phrase "outside the checkout" contains
  "checkout", so a subject read from the whole sentence is always found; a
  harness sentence ("at ``nice 19``, outside the checkout") would then read
  as a claim about where the site lives.  The subject is read with the
  phrase removed.
* A page may name **both** the inside and the outside form, because both are
  true of different rules: the site is inside the worktree, and its state
  is not tracked by Git.  Each form is read separately, and each is compared
  with the reading for that form, so a page that says both is judged once per
  form rather than once as a whole.
* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current.
* The check runs the real command, so it is bounded by what the clone needs
  to plant.  When a placement cannot be exercised at all, the reading is
  UNKNOWN (exit 3), never a pass.
* The check compares the *rule*, not the wording.  A page that describes the
  rule correctly in its own words still passes, because the check looks for
  the placement the page states, not for a sentence it expects.

Exit status:
  0  every stated placement matches the committed tree
  2  a shipped page states a placement the committed tree does not apply
  3  the question cannot be decided: no tree, no git, or the tree cannot be run
"""
from __future__ import annotations

import argparse
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

# The phrases a page may use to state where the site home lies.  Each entry is
# (marker, placement) where placement is "inside" or "outside".
STATEMENTS = (
    (re.compile(r"\boutside the (shared source |source )?repository\b", re.I), "outside"),
    (re.compile(r"\boutside the (checkout|worktree|git repository)\b", re.I), "outside"),
    (re.compile(r"\bnot inside the (checkout|worktree|repository)\b", re.I), "outside"),
    (re.compile(r"\binside the (shared source |source )?repository\b", re.I), "inside"),
    (re.compile(r"\binside the (checkout|worktree|git repository)\b", re.I), "inside"),
    (re.compile(r"\bwithin the (checkout|worktree|repository)\b", re.I), "inside"),
)

# What the tree's reading means for the placement a page states.
#
# A page states a **placement**: where the site is.  The tree produces an
# **outcome**: whether it accepts a site there.  The two are joined here, so
# the comparison is explicit rather than a naming coincidence: a page that
# says the site is inside the worktree is kept only when the tree accepts a
# site inside a worktree, and a page that says it is outside is kept only
# when the tree accepts a site outside one.
PLACEMENT_OUTCOME = {"inside": "accepted", "outside": "accepted"}

# The rule this check reads: **where the site home may be planted**.
#
# The scope is narrow on purpose.  A page states many rules about a site --
# that it is ignored by Git, that it holds the tape, that a clone does not
# inherit another instance's state -- and each of those is a rule about its own
# property, not about *where the directory may sit*.  Reading them all would
# make this check cry wolf on every page that mentions a site at all.

# A sentence that talks about the site's location at all.
PLACEMENT_SUBJECT = re.compile(
    r"(\bsite\b|\bplant\b|\bstate\b|\bhome\b|\bcheckout\b|\brepository\b|"
    r"\bworktree\b|\bplanting\b)", re.I)

# A sentence that says where the site is, rather than what it holds.
# The boundaries matter: "sits?" must not match the "sit" inside "site", or
# every sentence naming a site reads as a placement claim.  "lives" is the
# verb, not the adjective "live": `\blives?\b` matched "the live plant" and
# "a live consumer", so a harness sentence ("run ... from outside the
# checkout") in a paragraph that also said "live" was read as a placement
# claim the page never made (rust-plan §10.24, 2026-11-11).
PLACEMENT_PREDICATE = re.compile(
    r"(\bkeeps?\b|\blives\b|\bsits?\b|\blies?\b|\bbelongs?\b|"
    r"\bis (placed|planted|created|located)\b|"
    r"\bare (placed|planted|created|located)\b|"
    r"\bmust sit\b|\bhas to sit\b)", re.I)

BLOCK = re.compile(r"\n\s*\n")
FENCE = re.compile(r"```.*?```", re.DOTALL)
BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s")

# Words that negate the placement phrase they precede.
NEGATION = re.compile(
    r"\b(never|not|no|nothing|neither|nor|cannot|can't|isn't|is not|"
    r"does not|doesn't|do not|won't|will not|rather than|instead of)\b", re.I)

# A sentence, for reading a placement.  The split keeps the terminating
# punctuation so a reader can see where the sentence ended.
SENTENCE = re.compile(r"(?<=[.;:])\s+|\n")


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-placement-")
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
    """The claims a block states, one per bullet or paragraph line.

    Markdown hard-wraps a paragraph, so the lines of one claim are several
    physical lines.  A bullet starts a new claim; the lines that follow it,
    indented or not, belong to that claim.  Joining them first is what lets a
    reader's claim be read as the reader wrote it.
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


def _stated_placements(page: Path) -> list[dict]:
    """The placements a page states for the site home.

    A fenced block is an example, not a claim about the rule, so it is not
    read.

    The unit of reading is the **claim**, not the paragraph and not the
    sentence.  A paragraph can state several placements -- the site is inside
    the worktree, and its state is not tracked by Git -- and those are
    different claims with different answers.  Reading the block would attach
    one form's answer to the other form's claim.
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
            if not PLACEMENT_SUBJECT.search(unit):
                continue
            if not PLACEMENT_PREDICATE.search(unit):
                continue
            for placement in _placements_of(unit):
                found.append({"placement": placement,
                              "claim": " ".join(unit.split())[:300]})
    return found


def _negated(sentence: str, match: re.Match) -> bool:
    """Whether the placement phrase is negated in its sentence.

    The negation is read from the words between the start of the clause and
    the phrase itself, so "the state is never outside the checkout" is read
    as a negation while "outside the checkout, the state lives in a
    directory" is not.  A word that negates anywhere earlier in the sentence
    would be too coarse: "not the repository, but the worktree" negates one
    noun and not the other.
    """
    head = sentence[:match.start()].lower()
    return bool(NEGATION.search(head))


def _placements_of(unit: str) -> list[str]:
    """The placements a claim states, one per sentence that states one.

    A claim can state both forms -- the site is inside the worktree, and its
    state is not tracked -- because they are about different properties.  So
    each sentence is read separately and each placement it states is reported.
    A sentence that states no placement is not read: the page describes the
    rule without saying where the site sits.
    """
    placements: list[str] = []
    for sentence in SENTENCE.split(unit):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not PLACEMENT_SUBJECT.search(sentence):
            continue
        for pattern, placement in STATEMENTS:
            match = pattern.search(sentence)
            if not match:
                continue
            # The phrase's own nouns are not evidence that the sentence is
            # about where the site lives: the phrase "outside the checkout"
            # *contains* a subject word, so a subject read from the whole
            # sentence is always found.  A harness sentence -- "at `nice 19`,
            # outside the checkout" -- then reads as a placement claim the
            # page never made, and the gate fails a correct page.  Read the
            # sentence with the phrase removed: what remains is the subject
            # the claim is actually about.
            remainder = sentence[:match.start()] + sentence[match.end():]
            if not PLACEMENT_SUBJECT.search(remainder):
                break
            # A negated statement is the **same** placement stated the other
            # way round: "the state is never outside the checkout" says the
            # site is inside, not outside.  Reading it as the bare phrase
            # would report the page's own correction as the defect it
            # replaced.
            if _negated(sentence, match):
                placements.append("inside" if placement == "outside" else "outside")
            else:
                placements.append(placement)
            break
    return placements


def _init_site(tree: Path, work: Path, layout: str) -> str:
    """Run the real ``init`` against one placement and report what happened.

    Returns "accepted", "refused" or "unknown".  "unknown" means the command
    failed for a reason the check cannot interpret -- the reading is then
    UNKNOWN, never a pass.
    """
    root = work / layout
    root.mkdir(parents=True, exist_ok=True)
    if layout == "outside":
        # A directory that is not inside any Git worktree.
        home = root / ".mishe-tauftauf"
    elif layout == "nested":
        # A worktree, and a site one level deeper than its root.
        subprocess.run([GIT, "init", "-q", str(root)], capture_output=True)
        home = root / "sub" / ".mishe-tauftauf"
    else:
        # A worktree, and a site directly inside it.
        subprocess.run([GIT, "init", "-q", str(root)], capture_output=True)
        home = root / ".mishe-tauftauf"
    home.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["MISHE_SEED_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "init"],
        env=env, capture_output=True, text=True, cwd=str(root))
    if result.returncode == 0:
        return "accepted"
    message = (result.stdout + result.stderr).lower()
    if "worktree" in message or "repository root" in message:
        return "refused"
    return "unknown"


def observe(tree: Path) -> dict:
    """Observe what the committed tree does with each site placement."""
    work = Path(tempfile.mkdtemp(prefix="shipped-book-placement-run-"))
    try:
        return {layout: _init_site(tree, work, layout)
                for layout in ("inside", "outside", "nested")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def audit(tree: Path) -> dict:
    """Find pages that state a placement the committed tree does not apply."""
    if _package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    readings = observe(tree)
    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        for statement in _stated_placements(page):
            checked += 1
            # A claim about a placement is judged against the reading for
            # *that* placement: a page that says the site is inside the
            # worktree is judged against the inside reading, and one that
            # says it is outside is judged against the outside reading.
            # Comparing an inside claim with the outside reading would report
            # every correct page as stale.
            observed = readings[statement["placement"]]
            expected = PLACEMENT_OUTCOME[statement["placement"]]
            if observed == expected:
                continue
            findings.append({
                "file": source,
                "stated": statement["placement"],
                "observed": observed,
                "claim": statement["claim"],
            })
    return {"book_pages": len(pages), "statements": checked,
            "readings": readings, "findings": findings}


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
        readings = result["readings"]
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} "
              f"statements={result['statements']} "
              f"observed inside={readings['inside']} "
              f"outside={readings['outside']} "
              f"nested={readings['nested']}")
        for item in result["findings"]:
            print(f"  STALE PLACEMENT RULE  {item['file']} states the site is "
                  f"{item['stated']} the repository, but the committed tree "
                  f"{item['observed']} it")
            print(f"    {item['claim']}")
        if "unknown" in readings.values():
            print("UNKNOWN — the committed tree could not be exercised, so the "
                  "rule it applies is not observed")
            return 3
        if not result["findings"]:
            print("OK every stated site placement matches the committed tree")
            return 0
        print("FAIL a shipped page states a site placement the committed tree "
              "does not apply")
        return 2
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"UNKNOWN — shipped-book placement check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
