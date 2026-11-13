#!/usr/bin/env python3
"""Reject stale command-shaped references in the maintained reader corpus.

`check_leaf_invocation_coverage.py` checks CLI leaves missing from the book;
this checks the reverse direction. It scans backticked spans and fenced shell
lines in README.md and docs/*.md against the served CLI's argparse tree.
Unknown subcommands are stale claims. Prose is excluded because command tops
such as `wall`, `seed` and `pain` are also ordinary words.

The corpus is the **committed** pages — the bytes a clone of this repository
receives — not the working tree.  Commit ``237db71f`` (2026-10-30) added
``.gitignore`` rules for seven reader pages; five are still checkout-local and
two — ``protocol-boundaries.md`` and ``wall-coordination.md`` — were committed
again at ``d6f3266f`` (2026-11-06) and are in the clone.  The working tree
therefore holds more reader pages than a clone receives, so a stale command
claim in a page the clone never receives is not the reader's problem while one
in a page it does receive is.  The corpus is therefore read through
``git archive``, as ``checks/shipped-book-links.py`` does.

A non-site command-shaped span may be recorded in DISPOSITIONS. An
undispositioned finding fails the check. The scan is deliberately lexical:
plain prose, continued shell commands and unrecognized wrapper forms remain
known limits, not proof that the corpus is clear.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_artifact  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEAF_CHECKER = ROOT / "tools" / "check_leaf_invocation_coverage.py"

# No disposition is recorded here.  The one that stood for
# ``docs/protocol-boundaries.md`` is gone: the span it keyed —
# ``tmux list-panes`` — no longer appears in the corpus (it was dropped when
# that page was rewritten), and a ``tmux`` top is excluded by
# ``RAW_BINARY_TOPS`` before any disposition is consulted, so the key could
# never fire.  A dead key would read as covered coverage.
DISPOSITIONS: dict[tuple[str, str], str] = {}
CLI_NAME_MARKS = ("mishe", "mishe-tauftauf", "mishe_tauftauf")
"""Command name endings that mark a site-CLI invocation in a docs span.

The short ``mishe`` entry point is the standard name for ordinary commands;
the historical long name still reaches the same CLI for legacy call sites,
and ``mishe_tauftauf`` is the Python module name used by ``python -m`` forms.
"""
WRAPPERS = {"python", "python3", "rtk", ".venv/bin/python"}
RAW_BINARY_TOPS = {"tmux"}
PLACEHOLDER = re.compile(r"^([A-Z][A-Z0-9_./-]*|<[^>]*>|\[[^\]]*\])$")


def load_leaf_checker():
    spec = importlib.util.spec_from_file_location("leaf_checker", LEAF_CHECKER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load leaf checker: {LEAF_CHECKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def leaf_trie(src: Path) -> tuple[dict, set[str]]:
    module = load_leaf_checker()
    trie: dict = {}
    for leaf in module.leaves(module.load_parser(src)):
        node = trie
        for token in leaf.split():
            node = node.setdefault(token, {})
    return trie, set(trie)


def candidate_spans(text: str) -> list[str]:
    spans = re.findall(r"`([^`\n]+)`", text)
    for fence in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        for line in fence.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                spans.append(line)
    return spans


def invocation_start(tokens: list[str], tops: set[str]) -> int | None:
    """Return the token index where a site-CLI invocation begins."""
    for i, token in enumerate(tokens):
        if token not in tops:
            continue
        if i == 0:
            return None if token in RAW_BINARY_TOPS else i
        previous = tokens[i - 1]
        if previous in WRAPPERS or previous.endswith(CLI_NAME_MARKS):
            return i
    for i, token in enumerate(tokens):
        if token not in WRAPPERS and not token.endswith(CLI_NAME_MARKS):
            continue
        j = i + 1
        while j < len(tokens):
            if tokens[j].startswith("-"):
                j += 2
            elif tokens[j] in tops:
                return j
            else:
                j += 1
    return None


def walk(tokens: list[str], start: int, trie: dict) -> str | None:
    """Return the first unknown subcommand token, or None for a valid leaf."""
    node = trie
    i = start
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-"):
            i += 2
            continue
        if PLACEHOLDER.fullmatch(token):
            return None
        if token in node:
            node = node[token]
            i += 1
            continue
        return token if node else None
    return None


def audit(home: Path, corpus: dict[str, str] | None = None) -> dict:
    module = load_leaf_checker()
    _, served_sha, src = module.served_release_src(home)
    trie, tops = leaf_trie(src)
    if corpus is None:
        corpus = module.reader_corpus(home.resolve().parent)
    findings = []
    for name in sorted(corpus):
        for span in candidate_spans(corpus[name]):
            tokens = span.split()
            start = invocation_start(tokens, tops)
            if start is None:
                continue
            offending = walk(tokens, start, trie)
            if offending is None:
                continue
            entry = {"file": name, "span": span, "token": offending,
                     "position": tokens.index(offending, start)}
            disposition = DISPOSITIONS.get((name, span))
            if disposition:
                entry["disposition"] = disposition
            findings.append(entry)
    return {
        "served_sha": served_sha,
        "served_src": str(src),
        "corpus_files": len(corpus),
        "corpus_bytes": sum(len(text) for text in corpus.values()),
        "findings": findings,
        "dispositioned": [f for f in findings if "disposition" in f],
        "undispositioned": [f for f in findings if "disposition" not in f],
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=ROOT / ".mishe-tauftauf")
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository whose committed bytes are the clone's "
                             "reader corpus (default: the checkout root)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    module = load_leaf_checker()
    repo = (args.repo or ROOT).resolve()
    corpus = module.committed_reader_corpus(repo)
    if not corpus:
        print(f"UNKNOWN — no committed reader corpus could be read from {repo}; "
              f"the clone's book cannot be audited", file=sys.stderr)
        return 2

    result = audit(args.home, corpus)
    # A disposition keyed to a page this clone does not receive can never fire
    # here.  It is reported rather than deleted, so the artifact shows which
    # recorded reasons are currently unreachable instead of looking complete.
    unreachable = sorted({page for page, _ in DISPOSITIONS
                          if page not in corpus})
    result["corpus_source"] = "committed bytes of HEAD (what a clone receives)"
    result["dispositions_on_unshipped_pages"] = unreachable
    out = args.out or args.home / "artifacts" / "check-docs-command-names.json"
    out, status, detail = report_artifact.write_report(
        out, json.dumps(result, indent=1) + "\n")
    if status != "written":
        print(f"note — {detail}", file=sys.stderr)

    print(f"served release {result['served_sha']}")
    print(f"reader corpus: {result['corpus_files']} files, {result['corpus_bytes']} bytes "
          f"(committed bytes of HEAD)")
    for finding in result["findings"]:
        mark = "dispositioned" if "disposition" in finding else "STALE COMMAND"
        print(f"{mark}: {finding['file']}: {finding['span']!r} -> unknown {finding['token']!r}")
        if "disposition" in finding:
            print(f"    {finding['disposition']}")
    print(f"named-command findings {len(result['findings'])}; undispositioned {len(result['undispositioned'])}")
    if unreachable:
        print(f"recorded dispositions on pages this clone does not receive: "
              f"{' '.join(unreachable)}")
    print(f"artifact: {out}")
    return 1 if result["undispositioned"] else 0


if __name__ == "__main__":
    sys.exit(main())
