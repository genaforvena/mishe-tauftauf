"""Which roles a site has: the charters that exist, nothing else."""
from __future__ import annotations

from pathlib import Path


def declared(home) -> frozenset[str]:
    try:
        return frozenset(path.stem for path in (Path(home) / "charters").glob("*.md"))
    except OSError:
        return frozenset()


DEFAULT = ("genome", "witness", "senses", "discover", "health", "docs", "research-methods")


def install_defaults(home) -> list[str]:
    """Copy the packaged charter for each default role that has none. Never overwrites."""
    charters = Path(home) / "charters"
    charters.mkdir(parents=True, exist_ok=True)
    installed = []
    for role in DEFAULT:
        target = charters / f"{role}.md"
        if not target.exists():
            target.write_text((Path(__file__).with_name(f"seed_{role}_charter.md")).read_text(encoding="utf-8"),
                              encoding="utf-8")
            installed.append(role)
    return installed
