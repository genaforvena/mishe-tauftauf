#!/usr/bin/env python3
"""Report which reader pages name the served release's command invocations.

The book's standing rule is to name the command, not only the practice: a
reader-facing capability must carry the vocabulary a reader's goal will use.
This checker is the recurring, deterministic form of that rule. It walks the
argparse tree of the release this site actually serves and matches every leaf
invocation (`top sub`, or a bare `top`) against the reader corpus
(`README.md` plus `docs/*.md` in this checkout).

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

Exit status: 0 for a completed audit, 2 when the audit is unavailable
(UNKNOWN). A complete audit never fails on coverage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HEX = frozenset("0123456789abcdef")


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


def audit(root: Path, src: Path) -> dict:
    """Leaf-invocation coverage of the reader corpus against the served tree."""
    raw = leaves(load_parser(src))
    # `access` carries the alias `permit`, so the tree enumerates both forms
    # of every access leaf; merge alias forms onto their canonical access
    # name before matching.
    invocations = sorted({i.replace("permit ", "access ", 1) for i in raw})
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
        "invocations_strict_zero": len(zero),
        "strict_zero": zero,
        "bare_tops_strict_zero": tops_zero,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=REPO / ".mishe-tauftauf",
                        help="site home holding the served-release record")
    parser.add_argument("--root", type=Path, default=REPO,
                        help="checkout root holding the reader corpus")
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

    try:
        result = audit(args.root, served_src)
    except (ImportError, OSError, ValueError, SyntaxError) as exc:
        print(f"UNKNOWN — served release {sha} could not be audited: {exc}",
              file=sys.stderr)
        return 2

    record = {"served_sha": sha, "release_root": str(release_root),
              "served_src": str(served_src), "corpus_root": str(args.root),
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
          f"{result['corpus_bytes']} bytes")
    print(f"leaf invocations {result['invocations_total']} "
          f"({result['invocations_raw_with_aliases']} with alias forms); "
          f"named {result['invocations_covered']}; "
          f"uncovered {result['invocations_strict_zero']} "
          f"— coverage rows, not failures")
    print("uncovered leaf invocations:")
    for inv in result["strict_zero"]:
        print(f"  {inv}")
    print(f"bare tops never named in the corpus: "
          f"{' '.join(result['bare_tops_strict_zero']) or '(none)'}")
    print(f"artifact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
