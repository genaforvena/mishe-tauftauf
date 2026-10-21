#!/usr/bin/env python3
"""Detector: every landed field-review review satisfies the landing contract.

The field-review lane reads one real source per (field x angle) cell and lands
one review. Its failure mode is the retired mesh's: 245 of 376 reviews there
ended "uncommitted in the tree (steward lands)" -- prose on disk, no change
in the plant, and nothing to say so. A lane that is not observed is a lane
that decays into a reading pile.

This check answers the effect-level question on the repository's own state:
does every review the branch carries satisfy the contract the lane publishes?

The contract is read from the tracked tool rather than restated here, so the
check and the lane cannot disagree about what the contract is. The tool is
loaded by path from the reviewed tree, exactly as the plant's other checks
load their own helpers.

Exit status:
  0  every landed review satisfies the landing contract
  2  a landed review breaks the contract
  3  the question cannot be decided: no reviewed tree, no tracked tool, an
     unreadable registry, or a review that cannot be read
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

TOOL_RELATIVE = "research/field-review/field_review.py"
REGISTRY_RELATIVE = "research/field-review/fields.json"


def load_tool(repo: Path):
    """Load the reviewed tree's tool, not the working tree's.

    The runner runs every check from its committed HEAD bytes so that an
    uncommitted edit cannot change what the plant's monitoring reads. The
    tool is part of the reviewed tree, so it is read the same way: from HEAD.
    """
    path = repo / TOOL_RELATIVE
    if not path.is_file():
        raise FileNotFoundError(f"the tracked field-review tool is missing: {path}")
    try:
        result = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{TOOL_RELATIVE}"],
                                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"the reviewed tool cannot be read: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git show failed"
        raise FileNotFoundError(f"the reviewed tool is not on the branch: {detail}")
    spec = importlib.util.spec_from_file_location("field_review_tool", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the field-review tool: {path}")
    module = importlib.util.module_from_spec(spec)
    # The tool's dataclasses resolve their annotations through the module's own
    # entry, so the module is registered before it executes.
    sys.modules[spec.name] = module
    source = result.stdout.decode("utf-8", "replace")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        tool = load_tool(repo)
    except (OSError, ImportError, ValueError) as exc:
        print(f"UNKNOWN — the field-review contract cannot be read: {exc}")
        return 3
    try:
        tool.load_registry(repo / REGISTRY_RELATIVE)
    except (OSError, ValueError) as exc:
        print(f"UNKNOWN — the field-review grid cannot be read: {exc}")
        return 3
    try:
        problems = tool.validate_tree(repo)
    except (OSError, ValueError) as exc:
        print(f"UNKNOWN — the landed reviews cannot be checked: {exc}")
        return 3
    if problems:
        for name in sorted(problems):
            print(f"FAIL {name}")
            for problem in problems[name]:
                print(f"  {problem}")
        print(f"field-review landing contract broken by {len(problems)} review(s); "
              "resolver=field-review")
        return 2
    landed = len(tool.landed_review_files(repo))
    print(f"field-review landing contract holds for {landed} landed review(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
