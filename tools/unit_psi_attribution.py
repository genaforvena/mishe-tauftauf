"""Per-unit attribution over the operator cgroup sampler JSONL (read-only CLI view).

Thin view over mishe_tauftauf.unit_psi_reading: the reader owns the tail read,
the swap reading and the attribution rules. This file only decodes CLI arguments
and prints JSON. It writes nothing.

Usage: python tools/unit_psi_attribution.py [PATH] [--window-s N] [--top N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mishe_tauftauf.unit_psi_reading import WINDOW_S, attribute, read_tail  # noqa: E402

DEFAULT_PATH = ROOT / ".mishe-tauftauf" / "operator" / "unit-psi.jsonl"
DEFAULT_TOP = 3


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-unit PSI attribution over the operator cgroup sampler (read-only).")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH))
    parser.add_argument("--window-s", type=float, default=WINDOW_S)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(json.dumps({"state": "unknown", "reason": f"absent: {path}"}))
        return 0
    try:
        records, malformed = read_tail(path)
    except OSError as exc:
        print(json.dumps({"state": "unknown", "reason": f"read failed: {exc.__class__.__name__}"}))
        return 0
    out = attribute(records, window_s=args.window_s, top=args.top)
    out["malformed"] = malformed
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
