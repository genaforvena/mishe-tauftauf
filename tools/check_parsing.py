#!/usr/bin/env python3
"""Reject new parsing sites outside named adapters; expose frozen legacy debt."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

METHODS = frozenset({
    "split", "rsplit", "splitlines", "partition", "rpartition", "startswith", "endswith",
    "strip", "lstrip", "rstrip", "removeprefix", "removesuffix", "decode",
    "match", "fullmatch", "search", "findall", "finditer", "sub", "subn",
    "loads", "raw_decode", "fromisoformat", "strptime", "parse_args", "parse_known_args",
})
MODULE_CALLS = frozenset({
    "re.compile", "re.escape", "json.load", "csv.reader", "csv.DictReader",
    "urllib.parse.urlparse", "urllib.parse.urlsplit", "urllib.parse.parse_qs",
    "urllib.parse.parse_qsl", "shlex.split", "shlex.shlex",
})


@dataclass(frozen=True)
class ParsingSite:
    path: str
    line: int
    operation: str
    fingerprint: str


@dataclass
class CheckResult:
    violations: list[str]
    legacy_count: int


def inspect_source(path: str, source: str) -> list[ParsingSite]:
    tree = ast.parse(source, filename=path)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        return "?"
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operation = name(node.func)
        if operation in MODULE_CALLS or operation.rsplit(".", 1)[-1] in METHODS:
            fingerprint = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            sites.append(ParsingSite(path, node.lineno, operation, fingerprint))
    return sorted(sites, key=lambda item: (item.path, item.line))


def _policy(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "boundaries", "legacy"} or data["version"] != 1:
        raise ValueError("invalid parsing policy")
    boundaries, legacy = data["boundaries"], data["legacy"]
    if not isinstance(boundaries, list) or not boundaries or not isinstance(legacy, dict):
        raise ValueError("parsing policy needs explicit boundaries and a legacy budget")
    for name in [*boundaries, *legacy]:
        if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
                or not name.endswith(".py")):
            raise ValueError("policy paths must name literal relative Python files")
    if len(set(boundaries)) != len(boundaries) or set(boundaries) & set(legacy):
        raise ValueError("boundary and legacy paths must be distinct")
    for budgets in legacy.values():
        if not isinstance(budgets, dict):
            raise ValueError("invalid legacy budget")
        for digest, count in budgets.items():
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                    or isinstance(count, bool) or not isinstance(count, int) or count <= 0):
                raise ValueError("invalid legacy fingerprint/count")
    return data


def check(root: Path, policy_path: Path) -> CheckResult:
    policy = _policy(policy_path)
    root = root.resolve()
    if not (root / "src").is_dir():
        raise ValueError("source directory missing")
    for name in [*policy["boundaries"], *policy["legacy"]]:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"policy source missing or outside repository: {name}")
    sites = []
    for directory in ("src", "coordination", "tools"):
        for path in sorted((root / directory).rglob("*.py")):
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"source escapes repository: {path}")
            sites.extend(inspect_source(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
    budgets = {name: Counter(counts) for name, counts in policy["legacy"].items()}
    violations, legacy_count = [], 0
    for site in sites:
        if site.path in policy["boundaries"]:
            continue
        budget = budgets.get(site.path, Counter())
        if budget[site.fingerprint] > 0:
            budget[site.fingerprint] -= 1
            legacy_count += 1
        else:
            violations.append(f"{site.path}:{site.line}: parsing outside boundary: {site.operation}")
    return CheckResult(violations, legacy_count)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        result = check(args.root, args.root / "parsing-policy.json")
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"UNKNOWN — parsing guard unavailable: {exc}", file=sys.stderr)
        return 2
    if result.violations:
        print("\n".join(result.violations), file=sys.stderr)
        return 1
    print(f"PASS — no new parsing outside boundaries; {result.legacy_count} existing legacy sites remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
