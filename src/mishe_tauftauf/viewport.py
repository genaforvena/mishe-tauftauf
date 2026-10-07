"""Terminal-only continuation of a complete canonical dashboard."""
from __future__ import annotations

import hashlib
import math

from wcwidth import iter_graphemes, wcswidth


def _rows(text: str, columns: int) -> list[str]:
    """Hard-wrap without dropping spaces or splitting a grapheme.

    Control bytes are shown as escapes; they must not move the terminal cursor.
    The canonical dashboard retains the original bytes.
    """
    rows: list[str] = []
    for line in text.removesuffix("\n").split("\n"):
        safe = "".join(c if ord(c) >= 32 and ord(c) != 127 else ascii(c)[1:-1] for c in line)
        row = ""
        cells = 0
        for cluster in iter_graphemes(safe):
            width = wcswidth(cluster)
            if width < 0 or width > columns:
                raise ValueError("grapheme cannot fit terminal width")
            if cells + width > columns:
                rows.append(row)
                row, cells = "", 0
            row += cluster
            cells += width
        rows.append(row)
    return rows


def project(body: str, footer: str, slug: str, columns: int, lines: int, tick: int) -> str:
    """Keep chrome outside facts; rotate ordered pages of the current report.

    Every page identifies its body digest. Different digests are different frames,
    not parts of one immutable acquisition. Only the dashboard is the full oracle.
    Footer lines soft-wrap so tmux's joined capture can recover the exact lease.
    """
    if columns < 1 or lines < 1:
        return "UNKNOWN — terminal geometry unavailable"
    try:
        facts = _rows(body, columns)
        chrome = _rows(footer, columns)
        digest = hashlib.sha256(body.encode()).hexdigest()[:12]
        full = f"Full: pain read {slug} --launcher dashboard"
        # Reserve enough header cells for even a one-fact-row page count.
        largest = max(1, len(facts))
        header_size = len(_rows(f"VIEW {largest}/{largest} frame={digest}\n{full}", columns))
        capacity = lines - header_size - len(chrome)
        if capacity < 1:
            raise ValueError("no fact row fits alongside continuation and footer")
        count = max(1, math.ceil(len(facts) / capacity))
        page = tick % count
        header = _rows(f"VIEW {page + 1}/{count} frame={digest}\n{full}", columns)
        selected = facts[page * capacity:(page + 1) * capacity]
        padding = [""] * (lines - len(header) - len(selected) - len(chrome))
        # No final newline: the bottom row must not scroll facts off screen.
        return "\r\n".join(header + selected + padding) + "\r\n" + footer.rstrip("\n").replace("\n", "\r\n")
    except ValueError:
        warning = _rows("UNKNOWN — viewport too small; read full dashboard", max(1, columns))
        return "\r\n".join(warning[:lines])
