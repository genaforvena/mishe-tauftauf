#!/usr/bin/env python3
"""Compatibility entry point for the core planting module."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coordination.launcher import main  # noqa: E402


if __name__ == "__main__":
    main()
