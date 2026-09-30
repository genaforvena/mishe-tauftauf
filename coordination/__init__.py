"""Host-side coordination for checked updates across planted repositories."""

import sys
from pathlib import Path

# Host commands always use this checkout's core, not another installed release.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
