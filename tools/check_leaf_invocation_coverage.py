#!/usr/bin/env python3
"""Report which reader pages name the served release's command invocations.

The book's standing rule is to name the command, not only the practice: a
reader-facing capability must carry the vocabulary a reader's goal will use.
This checker is the recurring, deterministic form of that rule. It walks the
argparse tree of the release this site actually serves and matches every leaf
invocation (`top sub`, or a bare `top`) against the reader corpus
(`README.md` plus `docs/*.md`).

The corpus is the **committed** pages — the bytes a clone of this repository
receives.  The working tree and a clone differ here, and the difference
decides the verdict: commit ``237db71f`` ("Remove local operating records
from the public repository", 2026-10-30) gitignored seven reader pages that
still exist on this checkout, so the working tree shows 16 pages where a clone
shows 9.  Measured on 2026-11-01, the working tree's pages name 52 leaf
invocations and the clone's name 34; the 18 in between are named only by
pages a reader never receives.  So the corpus is read through ``git archive``,
exactly like ``checks/shipped-book-links.py``.  The working-tree reading is
reported beside it, because the gap between the two is the measurement.

The audited tree is resolved from the site's served-release record
(`SITE_HOME/health/runtime-release.json`), the same authority
`runtime_source.source_for` reads. The checker never falls back to a checkout
tree and never carries a literal release path, so it cannot keep auditing a
retired release while the pin moves. A missing, malformed or unreadable
record, a release whose CLI does not import, or an explicit `--src` that is
not the served release all exit 2 with the failure named: the audit must
never silently report coverage for code that is not what readers run.

Uncovered rows are reported as coverage, not as a failure count — an
invocation absent from every reader page is a naming gap to consider — and
the uncovered set is preserved raw, without narrowing the corpus.

Each uncovered leaf also carries a recorded disposition in `DISPOSITIONS`
below, because an uncovered row has exactly two honest endings: the page
should name a real reader-facing command, or the leaf is retired, internal or
install-time evidence that a reader page should not advertise. A leaf with no
disposition prints as `undispositioned` and stays a first-class gap, so newly
retired commands and new gaps both surface instead of shrinking the list
quietly.

Exit status: 0 for a completed audit, 2 when the audit is unavailable
(UNKNOWN). A complete audit never fails on coverage.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HEX = frozenset("0123456789abcdef")
GIT = "git"

# Why each uncovered leaf is uncovered, kept beside the check that enumerates
# them so the uncovered set can only ever change for a stated reason. Three
# shapes appear below: a retired command whose replacement the culture already
# uses, an internal or install-time surface no reader page should advertise, and
# a live reading whose reader-facing vocabulary lives on another command.
DISPOSITIONS: dict[str, str] = {
    # Retired branch-delivery workflow: `delivery` mutations raise at runtime;
    # `delivery show` reads the frozen history under SITE/deliveries/.
    "delivery check": "retired branch-delivery mutation; commit scoped work on main and verify runtime recovery",
    "delivery finish": "retired branch-delivery mutation; commit scoped work on main and verify runtime recovery",
    "delivery integrate": "retired branch-delivery mutation; commit scoped work on main and verify runtime recovery",
    "delivery retire": "retired branch-delivery mutation; commit scoped work on main and verify runtime recovery",
    "delivery submit": "retired branch-delivery mutation; commit scoped work on main and verify runtime recovery",
    "delivery show": "frozen branch-delivery history view; the workflow that wrote new records is retired",
    # Retired task ledger: walls and addressed messages replaced claim/step;
    # only the historical views below still read anything.
    "task add": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task event": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task finish": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task landing": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task offer": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task reopen": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task step": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task wait": "retired task-ledger mutation; walls and addressed messages replaced it",
    "task show": "retired-ledger history view over the tape; the wall is the current surface",
    "task coordination-report": "deterministic tape-wide coordination check; the witness pane publishes its readings",
    "task landing-status": "retired landing report; CI and live-consumer checks carried its reading",
    "task production-check": "retired landing report; CI and live-consumer checks carried its reading",
    # Internal or install-time surfaces a reader page should not advertise.
    "pain render": "headless frame compose for a renderer probe; readers use `pain read`",
    "seed init": "lower-level site bootstrap; a reader plants with `python3 -m coordination.launcher`",
    "seed start": "raises one resident pane for a manual setup; durable residents run `seed run` under generated services",
    "seed loop": "internal seed loop for launcher management; readers use `seed run`/`seed yield`",
    "engine explain": "shadow admission decision for a role; readers use `engine status`/`engine accounts`",
    "tmux stop": "teardown of an owned session, the paired inverse of `tmux start`; not a repair step",
    "dispatch-receipt": "bare top; an external dispatcher's delivery receipt with no live producer path in this plant",
    # Retired task-ledger mutation, refused at runtime with "task-ledger
    # mutation is retired"; the committed book's `wall dm`/`wall write` is the
    # replacement.  It is retired on the served release too, so naming it
    # would advertise a command that refuses.
    "task claim": "retired task-ledger mutation; walls and addressed messages replaced it",
    # The permission ledger's own surfaces.  The book names `access list` for
    # reading the ledger (docs/getting-started.md); the decisions below are
    # operator or in-plant procedure that the shipped reader pages do not
    # teach, so a clone's reader cannot act on them from the book alone.
    "access menu": "interactive permission menu; the operator's console surface, not a documented reader step",
    "access check": "reads one permission request's state; a step of the operator's decision procedure, not a reader step",
    "access grant": "operator decision on a permission request; the operator's console surface, not a documented reader step",
    "access revoke": "operator decision on a permission request; the operator's console surface, not a documented reader step",
    "access request": "a mind's request for a capability; the plant's own request procedure, not a documented reader step",
    "access recover": "records a bounded recovery for a blocked request; the plant's own blocker procedure",
    "access resolve": "closes a recovery with checked evidence; the plant's own blocker procedure",
    "access recoveries": "lists recorded recoveries; the plant's own blocker bookkeeping",
    # Resident-loop surfaces.  The book teaches `seed status` (the role's own
    # wake state) and `seed yield` (settling it); the loop's own ticks are the
    # supervisor's internals.
    "seed tick": "one supervisor tick; the resident loop's internals, not a reader step",
    "seed follow": "follows one mind's idle launcher; a resident-loop internal, not a reader step",
    # Wall surfaces the book does not teach.  `wall write`, `wall show`,
    # `wall outcome` and `wall dm` are the documented ones.
    "wall retry": "explicit retry of a failed delivery; an in-plant recovery step, not a reader step",
    "wall silence": "suppresses a wall write for a role; an in-plant control, not a reader step",
    # Plant diagnostics and sampling.  The book teaches `pain read` for a
    # pane and `discover show` for the last scan.
    "doctor": "local-plant diagnostics (site in Git, feed contiguity, pane layout); a repair operator's surface, not a reader step",
    "pain list": "lists the resident slugs; the reader reaches a pane by name, so the list is a supervisor's surface",
    "pain watch": "streams one pane's frame; the reader reads a pane with `pain read`",
    "discover scan": "takes a fresh discovery sample; the supervisor takes it, and the reader reads the last scan with `discover show`",
    "tmux start": "raises the plant's tmux session; the launcher does this at plant time, so a reader does not start it",
    # Guard and disposition-milestone surfaces the operator or the in-plant
    # ledger drives.  `guard install|restore|revert` (07ae18b4, the
    # operator-authorized guarded restore) is the operator's own restore
    # machinery; `disposition clear` removes one disposition record from the
    # plant's ledger.  No reader page teaches them, and none is a
    # predict-shaped reader mismatch.
    "guard install": "operator-authorized guarded-restore steering; a repair operator's surface, not a documented reader step",
    "guard restore": "operator-authorized guarded-restore steering; a repair operator's surface, not a documented reader step",
    "guard revert": "operator-authorized guarded-restore steering; a repair operator's surface, not a documented reader step",
    "disposition clear": "removes one disposition record from the in-plant ledger; an in-plant control, not a reader step",
}


class PinUnavailable(Exception):
    """The served release could not be resolved from the site's record."""


def served_release_src(home: Path) -> tuple[Path, str, Path]:
    """Resolve (release root, sha, src/) the site serves, from its record.

    Shape validation mirrors the record `runtime_source.select_source`
    writes: version 1, a 40-character lowercase hex sha, and a source path
    naming an importable release. Authoring the record is the promote lane's
    contract (`checked_source` verifies the clean exact-SHA worktree there);
    this reads it and refuses an unusable one.
    """
    pin = home / "health/runtime-release.json"
    if not pin.is_file():
        raise PinUnavailable(f"no served-release record at {pin}")
    try:
        record = json.loads(pin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinUnavailable(f"unreadable served-release record {pin}: {exc}") from exc
    try:
        if record["version"] != 1:
            raise PinUnavailable(f"unsupported served-release record version {record['version']!r}")
        sha = record["sha"]
        if not isinstance(sha, str) or len(sha) != 40 or not set(sha) <= HEX:
            raise PinUnavailable(f"invalid served-release sha {sha!r}")
        source = record["source"]
        if not isinstance(source, str) or not source:
            raise PinUnavailable("invalid served-release record: no source path")
    except KeyError as exc:
        raise PinUnavailable(f"invalid served-release record {pin}: missing {exc}") from exc
    except TypeError as exc:
        raise PinUnavailable(f"invalid served-release record {pin}: {exc}") from exc
    src = Path(source) / "src"
    if not (src / "mishe_tauftauf" / "cli.py").is_file():
        raise PinUnavailable(f"served release {sha} has no importable CLI at {src}")
    return Path(source), sha, src


def load_parser(src: Path):
    """Import the served release's ``mishe_tauftauf.cli.parser``.

    The checkout's own package must not answer for the served bytes, so the
    release's ``src`` leads ``sys.path`` and any already-imported
    ``mishe_tauftauf`` modules are dropped first.
    """
    sys.path.insert(0, str(src))
    for name in [m for m in list(sys.modules) if m.startswith("mishe_tauftauf")]:
        del sys.modules[name]
    from mishe_tauftauf.cli import parser as build_parser
    return build_parser()


def leaves(p, prefix: tuple[str, ...] = ()) -> list[str]:
    """Every leaf invocation: 'top sub' or bare 'top'; help=SUPPRESS skipped."""
    subs: list[tuple[str, argparse.ArgumentParser]] = []
    suppressed: set[str] = set()
    for act in p._actions:
        if isinstance(act, argparse._SubParsersAction):
            subs = list(act.choices.items())
            suppressed = {c.dest for c in act._choices_actions
                          if c.help == argparse.SUPPRESS}
    if not subs:
        return [" ".join(prefix)] if prefix else []
    out: list[str] = []
    for name, sub in subs:
        if name in suppressed:
            continue
        if any(isinstance(x, argparse._SubParsersAction) for x in sub._actions):
            out += leaves(sub, prefix + (name,))
        else:
            out.append(" ".join(prefix + (name,)))
    return sorted(set(out))


def reader_corpus(root: Path) -> dict[str, str]:
    """The maintained reader pages: README plus docs/*.md, as written now."""
    corpus: dict[str, str] = {}
    for md in [root / "README.md"] + sorted((root / "docs").glob("*.md")):
        try:
            corpus[str(md.relative_to(root))] = md.read_text(encoding="utf-8")
        except OSError:
            continue
    return corpus


def committed_reader_corpus(repo: Path) -> dict[str, str]:
    """The reader pages a clone of this repository actually receives.

    The working tree and a clone differ here, and the difference decides the
    verdict.  Commit ``237db71f`` ("Remove local operating records from the
    public repository", 2026-10-30) added the public guide
    ``docs/getting-started.md`` and, in the same commit, added the
    ``.gitignore`` rules that excluded ``docs/operating.md``,
    ``docs/how-it-works.md``, ``docs/wall-coordination.md``,
    ``docs/agent-step-experiments.md``, ``docs/decision-models.md``,
    ``docs/protocol-boundaries.md`` and ``docs/recovery.md``.  Those seven
    pages still exist on this checkout, so ``reader_corpus`` sees 16 files
    where a clone sees 9.

    Measured on this plant on 2026-11-01 (Docs wake 155786): the working-tree
    reading leaves **one** unnamed leaf, ``dispatch-receipt``, while the
    clone's reading leaves **19**.  Eighteen of the nineteen are named only by
    the gitignored pages — ``access grant``, ``access revoke``, ``access
    request``, ``access recover``, ``access resolve``, ``access check``,
    ``access menu``, ``access recoveries``, ``wall dm``, ``wall retry``,
    ``wall silence``, ``discover scan``, ``doctor``, ``pain list``, ``pain
    watch``, ``seed tick``, ``seed follow``, ``task claim`` and ``tmux
    start``.  A reader who clones the repository cannot find them.

    Later the same day the shipped book grew its own answer to one of them:
    ``docs/getting-started.md`` now names ``wall dm`` (Docs wake 155786,
    commit ``f03cd749``), so the clone's corpus covers it and the count fell
    to **18**.  A leaf that moves from the untracked-only set into the
    shipped book is the measure working: the gap closed because a reader can
    now find the command, not because the check narrowed.

    So the working-tree corpus answers a question no reader has: "does this
    checkout's local book name every command?".  The clone's corpus answers
    the reader's: "can a reader who cloned this find the command?".  This
    function reads the committed bytes through ``git archive``, exactly like
    ``checks/shipped-book-links.py``, so an untracked or gitignored working-tree
    page can never satisfy a check that describes the clone.

    A repository that cannot be read yields an empty corpus and the caller
    reports UNKNOWN rather than a clean audit of nothing.
    """
    tmp = tempfile.mkdtemp(prefix="reader-corpus-")
    try:
        archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                                 capture_output=True)
        if archive.returncode:
            return {}
        unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                                capture_output=True)
        if unpack.returncode:
            return {}
        return reader_corpus(Path(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def audit(root: Path, src: Path, corpus: dict[str, str] | None = None) -> dict:
    """Leaf-invocation coverage of a reader corpus against the served tree.

    ``corpus`` is the set of reader pages to audit.  It defaults to the pages
    of ``root`` as they are on disk; ``main`` passes the **committed** pages,
    because that is what a clone receives (see
    ``committed_reader_corpus``).  The corpus is never narrowed to make a
    verdict look better.
    """
    raw = leaves(load_parser(src))
    # `access` carries the alias `permit`, so the tree enumerates both forms
    # of every access leaf; merge alias forms onto their canonical access
    # name before matching.
    invocations = sorted({i.replace("permit ", "access ", 1) for i in raw})
    if corpus is None:
        corpus = reader_corpus(root)
    blob = "\n".join(corpus.values())
    covered, zero = [], []
    for inv in invocations:
        # the alias form counts as covering its canonical access leaf: docs
        # write `permit recover ...`, never `access recover`
        forms = [inv]
        if inv.startswith("access "):
            forms.append("permit " + inv[len("access "):])
        if any(f in blob for f in forms):
            covered.append(inv)
        else:
            zero.append(inv)
    # bare tops too: a two-word 'top sub' can be covered while 'top' alone is
    # never named
    tops = sorted({i.split()[0] for i in invocations})
    tops_zero = [t for t in tops if t not in covered and t not in blob]
    return {
        "corpus_files": sorted(corpus),
        "corpus_bytes": sum(len(v) for v in corpus.values()),
        "invocations_total": len(invocations),
        "invocations_raw_with_aliases": len(raw),
        "invocations_covered": len(covered),
        "covered": covered,
        "invocations_strict_zero": len(zero),
        "strict_zero": zero,
        "dispositioned": {inv: DISPOSITIONS[inv] for inv in zero
                          if inv in DISPOSITIONS},
        "undispositioned": [inv for inv in zero if inv not in DISPOSITIONS],
        "bare_tops_strict_zero": tops_zero,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=REPO / ".mishe-tauftauf",
                        help="site home holding the served-release record")
    parser.add_argument("--root", type=Path, default=REPO,
                        help="checkout root holding the local reader pages")
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository whose committed bytes are the clone's "
                             "reader corpus (default: --root)")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to audit instead of the "
                             "committed bytes")
    parser.add_argument("--src", type=Path,
                        help="accepted only when it is the served release")
    parser.add_argument("--out", type=Path,
                        help="JSON artifact path (default SITE_HOME/artifacts/)")
    args = parser.parse_args(argv)

    try:
        release_root, sha, served_src = served_release_src(args.home)
    except PinUnavailable as exc:
        print(f"UNKNOWN — {exc}", file=sys.stderr)
        return 2
    print(f"served release {sha} — {served_src}")

    if args.src is not None:
        requested = args.src.resolve()
        served = served_src.resolve()
        if requested != served:
            print(f"REFUSED — stale --src {requested} is not the served release; "
                  f"served release {sha} is {served}", file=sys.stderr)
            return 2
        print(f"--src {requested} is the served release {sha}")

    if args.tree:
        tree = args.tree.resolve()
        corpus = reader_corpus(tree)
        corpus_source = "an explicit tree"
        repo = None
    else:
        repo = (args.repo or args.root).resolve()
        try:
            corpus = committed_reader_corpus(repo)
        except (OSError, ValueError) as exc:
            print(f"UNKNOWN — committed reader corpus unavailable: {exc}",
                  file=sys.stderr)
            return 2
        corpus_source = "committed bytes of HEAD (what a clone receives)"
    if not corpus:
        # An empty corpus would audit nothing and report a clean book that
        # does not exist.  UNKNOWN is the honest reading.
        print(f"UNKNOWN — no reader corpus could be read; the clone's book "
              f"cannot be audited", file=sys.stderr)
        return 2

    try:
        result = audit(args.root, served_src, corpus)
    except (ImportError, OSError, ValueError, SyntaxError) as exc:
        print(f"UNKNOWN — served release {sha} could not be audited: {exc}",
              file=sys.stderr)
        return 2

    # The working-tree reading is kept beside the clone's, because the gap
    # between them is the measurement: a page that exists only here names
    # commands no reader of the repository can reach.
    working = None
    if repo is not None:
        try:
            working = audit(repo, served_src, reader_corpus(repo))
        except (ImportError, OSError, ValueError, SyntaxError) as exc:
            print(f"note — the working-tree reading was unavailable: {exc}",
                  file=sys.stderr)
    if working is not None:
        result["working_tree_corpus_files"] = len(working["corpus_files"])
        result["working_tree_corpus_bytes"] = working["corpus_bytes"]
        result["working_tree_undispositioned"] = working["undispositioned"]
        # A leaf the working tree names but the clone's corpus does not is
        # named only by a page this checkout holds and a clone does not.
        # Coverage, not the undispositioned count, is the measure: a leaf with
        # a recorded reason is still a command a reader cannot find.
        result["named_only_by_untracked_pages"] = sorted(
            set(working["covered"]) - set(result["covered"]))

    record = {"served_sha": sha, "release_root": str(release_root),
              "served_src": str(served_src), "corpus_root": str(args.root),
              "corpus_source": corpus_source,
              **result,
              "sample_time_utc": __import__("datetime").datetime.now(
                  __import__("datetime").timezone.utc).isoformat()}
    out = args.out or args.home / "artifacts" / "check-leaf-invocation-coverage.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"note — artifact not written to {out}: {exc}", file=sys.stderr)

    print(f"reader corpus: {len(result['corpus_files'])} files, "
          f"{result['corpus_bytes']} bytes ({corpus_source})")
    print(f"leaf invocations {result['invocations_total']} "
          f"({result['invocations_raw_with_aliases']} with alias forms); "
          f"named {result['invocations_covered']}; "
          f"uncovered {result['invocations_strict_zero']} "
          f"— coverage rows, not failures")
    print("uncovered leaf invocations, with a recorded disposition:")
    for inv, reason in result["dispositioned"].items():
        print(f"  {inv}: {reason}")
    print("uncovered leaf invocations with no disposition — a naming gap:")
    for inv in result["undispositioned"]:
        print(f"  {inv}")
    print(f"bare tops never named in the corpus: "
          f"{' '.join(result['bare_tops_strict_zero']) or '(none)'}")
    if working is not None:
        only_untracked = result.get("named_only_by_untracked_pages", [])
        print(f"working tree: {result['working_tree_corpus_files']} files, "
              f"{result['working_tree_corpus_bytes']} bytes; "
              f"leaves named by the clone's book {result['invocations_covered']} "
              f"vs {len(working['covered'])} by the working tree "
              f"— named only by pages this clone does not receive "
              f"({len(only_untracked)}): "
              f"{' '.join(only_untracked) or '(none)'}")
    print(f"artifact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
