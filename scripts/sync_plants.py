#!/usr/bin/env python3
"""Retry checked updates to the core checkout's registered plants."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coordination.site_sync import main  # noqa: E402


if __name__ == "__main__":
    main()
