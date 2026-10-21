#!/usr/bin/env python3
"""Detector: every landed field-review review satisfies the landing contract.

The field-review lane reads one real source per (field x angle) cell and lands
one review. Its failure mode is the retired mesh's: 245 of 376 reviews there
ended "uncommitted in the tree (steward lands)" -- prose on disk, no change
in the plant, and nothing to say so. A lane that is not observed is a lane
that decays into a reading pile.

This check answers the effect-level question on the REVIEWED tree: does every
review the branch carries satisfy the contract the lane publishes, and is any
review stuck on the disk waiting to land?

The contract is read from the reviewed tree's own tool rather than restated
here, so the check and the lane cannot disagree about what the contract is.
The tool's bytes, the grid, the reviews and the application targets are all
read from the branch: a dirty working tree must not be able to change what
the plant's monitoring reads, and a staged-but-uncommitted review must not
count as landed.

Exit status:
  0  every landed review satisfies the landing contract, and no review has
     waited on the disk past the grace period
  2  a landed review breaks the contract, a review has waited too long to
     land, or a landed review was edited after it landed
  3  the question cannot be decided: no reviewed tree, no tracked tool, an
     unreadable grid, or a review that cannot be read
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

TOOL_RELATIVE = "research/field-review/field_review.py"
GRID_RELATIVE = "research/field-review/fields.json"
REVIEWS_RELATIVE = "research/field-review/reviews"


def load_tool(repo: Path):
    """Load the reviewed tree's tool, not the working tree's.

    The runner runs every check from its committed HEAD bytes so that an
    uncommitted edit cannot change what the plant's monitoring reads. The
    tool is part of the reviewed tree, so it is read the same way: from HEAD.
    """
    try:
        result = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{TOOL_RELATIVE}"],
                                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"the reviewed tool cannot be read: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git show failed"
        raise FileNotFoundError(f"the reviewed tool is not on the branch: {detail}")
    spec = importlib.util.spec_from_file_location("field_review_tool", repo / TOOL_RELATIVE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the field-review tool: {TOOL_RELATIVE}")
    module = importlib.util.module_from_spec(spec)
    # The tool's dataclasses resolve their annotations through the module's own
    # entry, so the module is registered before it executes.
    sys.modules[spec.name] = module
    source = result.stdout.decode("utf-8", "replace")
    exec(compile(source, TOOL_RELATIVE, "exec"), module.__dict__)
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
    for name in ("head_bytes", "head_paths", "landed_review_names", "validate_review_text",
                 "unlanded_reviews", "edited_landed_reviews", "head_has_file", "load_registry"):
        if not hasattr(tool, name):
            print(f"UNKNOWN — the reviewed field-review tool does not provide {name}; "
                  "the branch's tool is older than this check")
            return 3
    # The grid is read from the branch too: a dirty grid must not decide what
    # the plant's monitoring calls a contract.
    grid_bytes = tool.head_bytes(repo, GRID_RELATIVE)
    if grid_bytes is None:
        print(f"UNKNOWN — the reviewed field-review grid is not on the branch: {GRID_RELATIVE}")
        return 3
    try:
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="field-review-grid-") as tmp:
            grid_path = Path(tmp) / "fields.json"
            grid_path.write_bytes(grid_bytes)
            tool.load_registry(grid_path)
    except (OSError, ValueError) as exc:
        print(f"UNKNOWN — the field-review grid cannot be read: {exc}")
        return 3
    problems: list[str] = []
    landed = 0
    try:
        for name in tool.landed_review_names(repo):
            blob = tool.head_bytes(repo, name)
            if blob is None:
                problems.append(f"{name}: the branch lists the review but cannot read its bytes")
                continue
            landed += 1
            text = blob.decode("utf-8", "replace")
            for problem in tool.validate_review_text(
                    name, text, repo,
                    target_exists=lambda target: tool.head_has_file(repo, target)):
                problems.append(f"{name}: {problem}")
        waiting = tool.unlanded_reviews(repo)
        for name, age in waiting:
            problems.append(f"{name}: a review has waited {age:.0f}s on the working tree "
                            "without landing on the branch")
        for name in tool.edited_landed_reviews(repo):
            problems.append(f"{name}: a landed review was edited after it landed")
    except (OSError, ValueError) as exc:
        print(f"UNKNOWN — the landed reviews cannot be checked: {exc}")
        return 3
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        print(f"field-review landing contract broken by {len(problems)} finding(s); "
              "resolver=field-review")
        return 2
    if landed == 0:
        print("field-review landing contract holds, but no review has landed yet; "
              "this is not evidence of a working round trip")
        return 0
    print(f"field-review landing contract holds for {landed} landed review(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
