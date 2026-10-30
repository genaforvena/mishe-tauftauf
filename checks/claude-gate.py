#!/usr/bin/env python3
"""Detector: every mind that runs on Claude Code is alive, answering and unblocked.

A Claude Code mind reports its state through lifecycle hooks, not a TUI the
plant can read (see src/mishe_tauftauf/claude_gate.py). The failures that
follow: the process dies or is replaced, a turn never reaches Stop (interrupt,
hang), a permission dialog waits for a human, the model call fails
(StopFailure: rate limit, auth), or a hook cannot write its state. Each is a
standing condition this check reads from the hook receipts and the pane facts.

The decision logic lives in the gate installed under SITE/claude-gate/, the
same bytes the probe and hooks run, so the check and the gate cannot disagree.
A missing or unloadable gate is UNKNOWN, never OK.

Exit status:
  0  no claude-engine role is declared, or every one is live and unblocked
  2  a claude-engine role has a standing problem (named in the verdict line)
  3  undecidable: gate not installed, or no plant tmux socket readable
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo")
    args = parser.parse_args()
    home = Path(args.home)
    gate = home / "claude-gate" / "claude_gate.py"
    try:
        spec = importlib.util.spec_from_file_location("claude_gate", gate)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code, lines = module.check(home)
    except (OSError, ImportError, AttributeError, SyntaxError, TypeError) as exc:
        print(f"UNKNOWN: claude gate unavailable ({gate}): {exc}")
        return 3
    print(("OK" if code == 0 else "FAIL" if code == 2 else "UNKNOWN") + ": " + "; ".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
