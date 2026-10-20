#!/usr/bin/env python3
"""Reject stale command-shaped references in the maintained reader corpus.

`check_leaf_invocation_coverage.py` checks CLI leaves missing from the book;
this checks the reverse direction. It scans backticked spans and fenced shell
lines in README.md and docs/*.md against the served CLI's argparse tree.
Unknown subcommands are stale claims. Prose is excluded because command tops
such as `wall`, `seed` and `pain` are also ordinary words.

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

ROOT = Path(__file__).resolve().parents[1]
LEAF_CHECKER = ROOT / "tools" / "check_leaf_invocation_coverage.py"

DISPOSITIONS = {
    ("docs/protocol-boundaries.md", "tmux list-panes"): (
        "the raw tmux binary's pane listing, decoded by proc_table.py; not a site-CLI invocation"
    ),
}
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


def audit(home: Path) -> dict:
    module = load_leaf_checker()
    _, served_sha, src = module.served_release_src(home)
    trie, tops = leaf_trie(src)
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
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = audit(args.home)
    out = args.out or args.home / "artifacts" / "check-docs-command-names.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")

    print(f"served release {result['served_sha']}")
    print(f"reader corpus: {result['corpus_files']} files, {result['corpus_bytes']} bytes")
    for finding in result["findings"]:
        mark = "dispositioned" if "disposition" in finding else "STALE COMMAND"
        print(f"{mark}: {finding['file']}: {finding['span']!r} -> unknown {finding['token']!r}")
        if "disposition" in finding:
            print(f"    {finding['disposition']}")
    print(f"named-command findings {len(result['findings'])}; undispositioned {len(result['undispositioned'])}")
    print(f"artifact: {out}")
    return 1 if result["undispositioned"] else 0


if __name__ == "__main__":
    sys.exit(main())
