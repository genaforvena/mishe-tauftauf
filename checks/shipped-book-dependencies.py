#!/usr/bin/env python3
"""Detector: a shipped page's third-party dependency claim must be the tree's.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a
page names exists in the clone.  ``checks/shipped-book-promises.py`` asks
whether the page a promise points at *contains* what the promise names.
``checks/shipped-book-runtimes.py`` asks whether a runtime a page describes is
reachable.  ``checks/shipped-book-evidence.py`` asks whether a stated evidence
rule is the rule the clone applies.  ``checks/shipped-book-placement.py`` asks
where the site may be planted.

None of them asks whether the **prerequisites a page lists** are the
prerequisites the clone actually has.  A page can tell a reader that the
runtime "has no required third-party Python dependencies" while the clone
needs one to start at all.  Every existing gate passes that page: the path it
names exists, the link resolves, the command is named.

Measured on this plant on 2026-11-01 (Docs wake 152920): the shipped
``docs/getting-started.md`` told a reader "The runtime has no required
third-party Python dependencies", and ``.agents/skills/plant-mishe/SKILL.md``
said the same.  But commit ``6004170f`` ("fix: paginate terminal dashboards
with reserved lease chrome", 2026-10-07) added
``src/mishe_tauftauf/viewport.py``, whose **module-level**
``from wcwidth import iter_graphemes, wcswidth`` is a hard import, and
``pyproject.toml`` declares ``dependencies = ["wcwidth>=0.9,<1"]``.  The
no-dependency claim was written later (``237db71f``, 2026-10-30) from the
plant's own checkout, where ``wcwidth`` happened to be installed.

So the reader's documented first command failed at the very end of planting,
with a refusal that names the dependency but not the fix:

    $ python3 -m coordination.launcher --engine-command 'codex'
    ...
    ValueError: selected runtime interpreter /usr/bin/python3 cannot load the
    renderer dependency wcwidth (No module named 'wcwidth'); panes would die
    at import -- activate the dashboard with an interpreter that has it
    installed

Everything before that point had succeeded: the site was created, the resident
windows were started, the discovery scan ran.  The reader is told to check a
list of prerequisites, and the one prerequisite that decides the outcome is
not on it.

The rule this check enforces:

* a shipped page may state the runtime's **third-party dependency set**;
* when it does, the statement must agree with the dependencies the
  **committed tree** actually requires -- not with the plant's own checkout,
  and not with any release.

The rule is read from the committed tree by **executing** it.  The tree's own
modules are imported with each candidate dependency blocked, and the ones that
cannot be imported are the ones the tree requires:

* **required** -- a distribution whose absence makes a module of the runtime
  fail to import.  The reader cannot run the tree without it.
* **optional** -- a distribution the tree imports only inside a function or
  behind a guard.  The tree imports it when a reader asks for that surface
  and survives without it.

The check does not read the tree's ``pyproject.toml`` for the answer.  That
file is metadata, and metadata drifts from the code: a distribution can be
declared and unused, or used and undeclared.  The answer is read by running
the tree with the dependency **absent** and observing what the tree does.

Scope and limits, named:

* Only pages that state a claim about the **runtime's** third-party
  dependency set are read.  A page that says a *component* has no
  third-party dependencies -- the completions-judge adapter, which is
  stdlib-only and really has none -- is not a claim about the runtime, and
  reading it would make the check cry wolf on every page that mentions a
  package.
* The stated claim is read from a small, explicit vocabulary of phrases
  ("no required third-party ... dependencies", "the only required third-party
  dependency is X").  A page that describes the rule in prose the vocabulary
  does not cover is not a finding, and that is a known limit rather than a
  cleared question.
* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current.
* Only a **module-level** import counts as required.  An import inside a
  function, or behind a guard, is optional by construction and the tree
  survives without it.
* Reachability is read from the tree's own imports: a module the runtime
  never imports cannot make the runtime require anything, however it imports
  it.
* The check reports the dependency set it observed.  It does not judge whether
  a named dependency is a good choice, and it does not check that the
  distribution is installable from the reader's platform.

Exit status:
  0  every stated third-party dependency claim matches the committed tree
  2  a shipped page states a dependency claim the committed tree does not have
  3  the question cannot be decided: no tree, no git, or the tree cannot be run
"""
from __future__ import annotations

import argparse
import ast
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

# The phrases a page may use to state the runtime's third-party dependency
# set.  Each entry is (marker, kind) where kind is "none" for a page that
# claims there are no required third-party dependencies, and "named" for a
# page that names them.
#
# "none" is the dangerous claim, because it is the one that tells a reader to
# stop checking.  A page that names the dependency is read as naming it, and
# the name is compared with the tree's.
STATEMENTS = (
    (re.compile(r"\bno required third[- ]party\b", re.I), "none"),
    (re.compile(r"\bno third[- ]party (python )?(dependencies|packages?)\b", re.I), "none"),
    (re.compile(r"\b(only|single) required third[- ]party\b", re.I), "named"),
    (re.compile(r"\b(only|single) third[- ]party (dependency|dependencies|package)\b", re.I), "named"),
)

# The claim is about the **runtime's** dependency set, so the sentence must
# name the runtime, the plant or the prerequisites.  A sentence about some
# other subject -- "This adapter has no third-party dependencies" -- is not a
# claim about the runtime, and the tree does not contradict it.
RUNTIME_SUBJECT = re.compile(
    r"(\bruntime\b|\bplant\b|\bplanting\b|\bprerequisit|\breader\b|"
    r"\binterpreter\b|\bpython\b)", re.I)

BLOCK = re.compile(r"\n\s*\n")
FENCE = re.compile(r"```.*?```", re.DOTALL)
BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s")
SENTENCE = re.compile(r"(?<=[.;:])\s+|\n")

# Words that are not distribution names.  These are the words a sentence uses
# around the claim, not a name a reader would install.
NOT_A_DISTRIBUTION = frozenset({
    "the", "a", "an", "and", "or", "not", "no", "any", "is", "are", "it",
    "its", "this", "that", "these", "those", "there", "their", "they",
    "runtime", "plant", "planting", "reader", "package", "packages",
    "dependency", "dependencies", "python", "interpreter", "prerequisite",
    "prerequisites", "requirement", "requirements", "only", "single",
    "third", "party", "installed", "install", "import", "importable",
    "required", "requires", "need", "needs", "must", "should", "if",
    "otherwise", "check", "checks", "installing", "version", "versions",
    "missing", "available", "then", "so", "with", "without",
    "from", "for", "to", "in", "on", "of", "by", "as", "at", "be",
})


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-dependencies-")
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
    indented or not, belong to that claim.
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


def _stated_claims(page: Path) -> list[dict]:
    """The dependency claims a page states.

    A fenced block is an example, not a claim about the rule, so it is not
    read.

    The unit of reading is the **claim**, not the paragraph and not the
    sentence.  A bullet list states several claims in one block -- "Python
    3.10 or newer", "no required third-party dependencies", "tmux" -- and
    those are different claims with different answers.
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
            for kind, names in _claims_of(unit):
                found.append({"kind": kind, "names": names,
                              "claim": " ".join(unit.split())[:300]})
    return found


def _claims_of(unit: str) -> list[tuple[str, list[str]]]:
    """The dependency claims a unit states.

    A claim is read only where the sentence names the runtime: a page may
    say that one adapter has no third-party dependencies, and the tree does
    not contradict that.
    """
    claims: list[tuple[str, list[str]]] = []
    for sentence in SENTENCE.split(unit):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not RUNTIME_SUBJECT.search(sentence):
            continue
        for pattern, kind in STATEMENTS:
            if not pattern.search(sentence):
                continue
            if kind == "none":
                claims.append(("none", []))
            else:
                # A named claim names its distribution in the same claim --
                # the bullet that names the package is the one that says it
                # is the only required third-party dependency.
                names = _distributions_of(unit)
                if names:
                    claims.append(("named", names))
            break
    return claims


def _distributions_of(text: str) -> list[str]:
    """The distributions a text names.

    Only a name in code ticks is read: prose names the dependency inside a
    tick, and a plain-prose name is too weak a signal to compare against an
    observed set.
    """
    names: list[str] = []
    for span in re.findall(r"`([^`\n]+)`", text):
        span = span.strip()
        if not span or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", span):
            continue
        if span.lower() in NOT_A_DISTRIBUTION:
            continue
        names.append(span)
    return names


def _module_map(package: Path) -> dict[str, Path]:
    """The runtime's modules, keyed by import name."""
    modules: dict[str, Path] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package).as_posix()[:-3].replace("/", ".")
        if rel.endswith(".__init__"):
            rel = rel[: -len(".__init__")]
        modules[f"mishe_tauftauf.{rel}" if rel else "mishe_tauftauf"] = path
    return modules


def _imports_of(path: Path) -> tuple[set[str], set[str]]:
    """The imports a module makes: local ones, and third-party ones.

    The third-party set is split by **where** the import is.  A module-level
    import is a hard dependency -- the module cannot be imported without it.
    An import inside a function, or behind a guard, is optional: the tree
    imports it only when a reader asks for that surface.
    """
    stdlib = set(sys.stdlib_module_names)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return set(), set()
    local: set[str] = set()
    hard: set[str] = set()
    soft: set[str] = set()

    def record(name: str, module_level: bool) -> None:
        top = name.split(".")[0]
        if top in stdlib or top == "__future__":
            return
        if top in {"mishe_tauftauf", "coordination"}:
            local.add(top)
            return
        (hard if module_level else soft).add(top)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name, isinstance(node, ast.Module))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            record(node.module, isinstance(node, ast.Module))
    return local, hard | soft


def _hard_imports(path: Path) -> set[str]:
    """The third-party distributions a module imports at module level."""
    stdlib = set(sys.stdlib_module_names)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            names = [node.module]
        for name in names:
            top = name.split(".")[0]
            if top in stdlib or top == "__future__":
                continue
            if top in {"mishe_tauftauf", "coordination"}:
                continue
            found.add(top)
    return found


def _reachable(tree: Path) -> tuple[set[str], dict[str, set[str]]]:
    """The runtime's reachable modules, and each one's hard dependencies.

    Reachability is read from the tree's own imports, starting at the
    package's entry points.  A module the runtime never imports cannot make
    the runtime require anything, however it imports it.
    """
    package = _package_dir(tree)
    if package is None:
        raise ValueError("no plant package in tree")
    modules = _module_map(package)
    entries = {name for name in modules
               if name.rsplit(".", 1)[-1] in {"cli", "__main__"}}
    if not entries:
        entries = set(modules)
    reachable: set[str] = set()
    stack = list(entries)
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        path = modules.get(name)
        if path is None:
            continue
        local, _ = _imports_of(path)
        for candidate in modules:
            if candidate == name:
                continue
            if candidate == local or candidate.startswith(name + "."):
                stack.append(candidate)
            elif candidate.split(".")[0] == name.split(".")[0]:
                stack.append(candidate)
    hard: dict[str, set[str]] = {}
    for name in reachable:
        path = modules.get(name)
        if path is not None:
            hard[name] = _hard_imports(path)
    return reachable, hard


def _import_fails_without(tree: Path, distribution: str) -> bool:
    """Whether a reachable module cannot be imported without a distribution.

    The tree is executed with the distribution blocked, so the answer is
    observed rather than asserted.  Blocking means raising the import error
    a reader without the package sees.
    """
    work = Path(tempfile.mkdtemp(prefix="shipped-book-dependency-block-"))
    try:
        blocker = work / "blocked"
        blocker.mkdir()
        (blocker / f"{distribution}.py").write_text(
            f"raise ImportError(\"No module named '{distribution}'\")\n",
            encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{blocker}{os.pathsep}{tree / 'src'}"
        package = _package_dir(tree)
        assert package is not None
        code = (
            "import importlib, sys\n"
            "failures = []\n"
            "for name in sys.argv[1:]:\n"
            "    try:\n"
            "        importlib.import_module(name)\n"
            "    except ImportError as exc:\n"
            "        failures.append((name, str(exc)))\n"
            "    except Exception as exc:\n"
            "        pass\n"
            "print('FAILURES', failures)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, *sorted(_module_map(package))],
            env=env, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise ValueError(f"could not import the tree with {distribution} blocked: "
                             f"{result.stderr.strip()[:300]}")
        for line in result.stdout.splitlines():
            if not line.startswith("FAILURES "):
                continue
            for name, message in eval(line[len("FAILURES "):]):  # noqa: S307
                if distribution.lower() in message.lower():
                    return True
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def observe(tree: Path) -> dict:
    """Observe which third-party distributions the committed tree requires."""
    reachable, hard = _reachable(tree)
    candidates = set()
    for names in hard.values():
        candidates |= names
    required: list[str] = []
    optional: list[str] = []
    for name in sorted(candidates):
        if _import_fails_without(tree, name):
            required.append(name)
        else:
            optional.append(name)
    return {"required": required, "optional": optional,
            "reachable_modules": len(reachable)}


def audit(tree: Path) -> dict:
    """Find pages that state a dependency claim the committed tree does not have."""
    if _package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    readings = observe(tree)
    required = readings["required"]
    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        for statement in _stated_claims(page):
            checked += 1
            if statement["kind"] == "none":
                if required:
                    findings.append({
                        "file": source, "stated": "none", "observed": required,
                        "claim": statement["claim"],
                    })
                continue
            # A page that names the dependency is kept only when it names the
            # one the tree requires.  Naming an optional dependency is not a
            # defect: a page may tell a reader that an extra package helps.
            named = {name.lower() for name in statement["names"]}
            if required and not (named & {name.lower() for name in required}):
                findings.append({
                    "file": source, "stated": sorted(named), "observed": required,
                    "claim": statement["claim"],
                })
    readings["book_pages"] = len(pages)
    readings["statements"] = checked
    readings["findings"] = findings
    return readings


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
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} statements={result['statements']} "
              f"reachable modules={result['reachable_modules']}")
        print(f"required third-party dependencies: "
              f"{', '.join(result['required']) or '(none)'}")
        print(f"optional third-party dependencies: "
              f"{', '.join(result['optional']) or '(none)'}")
        for item in result["findings"]:
            print(f"  STALE DEPENDENCY CLAIM  {item['file']} states {item['stated']}, "
                  f"but the committed tree requires {item['observed']}")
            print(f"    {item['claim']}")
        if not result["findings"]:
            print("OK every stated third-party dependency claim matches the committed tree")
            return 0
        print("FAIL a shipped page states a dependency claim the committed tree "
              "does not have")
        return 2
    except (OSError, ValueError, SyntaxError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"UNKNOWN — shipped-book dependency check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
