#!/usr/bin/env python3
"""Detector: every promoted component still matches its ledger, and no install drifts from it.

``docs/component-deploy.md`` names the two gaps this closes. First, nothing ran
``components.verify`` on a schedule, so a component that went RED after its
promotion was not routed to a resolver. Second, the site's own ``$SITE/bin/NAME``
install is a second copy that the component detector does not look at: until
component-deploy step 3 lands, consumers still read it, so a promotion and a
later hand-installed binary can drift apart silently.

For each promoted component this check runs ``components.verify`` and, when
``$SITE/bin/NAME`` exists, compares its sha256 with
``components/NAME/current/bin/NAME``. A digest mismatch means the bytes the
site runs are not the bytes the ledger promoted.

Exit status:
  0  GREEN: every promoted component verifies GREEN and every present install
     matches its component's bytes
  2  RED: a component verifies RED, or an install's digest differs from the
     promoted bytes
  3  UNKNOWN: no components directory, no promoted component, or the components
     module cannot be imported — an unobserved detector proves nothing

Usage: component-verify.py --home SITE_HOME [--repo REPO]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

GREEN, RED, UNKNOWN = 0, 2, 3


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(repo: Path):
    sys.path.insert(0, str(repo / "src"))
    from mishe_tauftauf import components

    return components


def evaluate(home: Path, repo: Path) -> tuple[int, list[str]]:
    """Return (exit code, human-readable lines) for the site at ``home``."""
    try:
        components = _load(repo)
    except Exception as exc:  # noqa: BLE001 - an unimportable detector cannot decide
        return UNKNOWN, [f"UNKNOWN: cannot import mishe_tauftauf.components from {repo}: {exc}"]

    root = Path(home) / "components"
    if not root.is_dir():
        return UNKNOWN, [f"UNKNOWN: {root} is not a directory"]
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    if not names:
        return UNKNOWN, [f"UNKNOWN: no component is promoted under {root}"]

    problems: list[str] = []
    lines: list[str] = []
    for name in names:
        verdict = components.verify(home, name)
        sha = verdict.get("sha") or "none"
        if verdict.get("state") != "GREEN":
            problems.extend(f"{name}: {p}" for p in verdict.get("problems") or ["not GREEN"])
            lines.append(f"{name}: RED ({sha})")
            continue
        install = Path(home) / "bin" / name
        promoted = components.current(home, name) / "bin" / name
        if install.is_file() and promoted.is_file():
            installed = _sha256(install)
            if installed != _sha256(promoted):
                problems.append(
                    f"{name}: install drift, $SITE/bin/{name} is {installed[:12]}… "
                    f"but the promoted bytes are {_sha256(promoted)[:12]}…"
                )
                lines.append(f"{name}: RED install drift ({sha})")
                continue
            lines.append(f"{name}: GREEN install matches ({sha[:12]}…)")
        else:
            lines.append(f"{name}: GREEN ({sha[:12]}…, no $SITE/bin install)")

    if problems:
        return RED, lines + [f"RED: {p}" for p in problems]
    return GREEN, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, help="site home")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]),
                        help="repository holding src/mishe_tauftauf")
    args = parser.parse_args(argv)
    code, lines = evaluate(Path(args.home), Path(args.repo))
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
