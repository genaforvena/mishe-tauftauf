"""Boundary adapter: decodes the Redmi10 observation stream's log text.

redmi10_stream consumes decoded rows only. Each stream row is an external text
representation ``<seq> <host_epoch_s> <kind> <key=value ...>``; the decoding of its
bytes, lines and tokens happens here and nowhere else.
"""
from __future__ import annotations


def decode_tail(data: bytes, *, at_file_start: bool) -> list[str]:
    """Complete lines of a tail read; a partial first line is dropped unless the read began at the file start."""
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines if at_file_start or not lines else lines[1:]


def parse_row(line: str) -> dict | None:
    """Decode one stream row, or None when it is not a row this reader understands."""
    parts = line.split(" ", 2)
    if len(parts) < 3:
        return None
    try:
        epoch = float(parts[1])
    except ValueError:
        return None
    tokens = parts[2].split(" ")
    # Some rows lead with a key=value token (for example wifi_link_rssi_dbm=-56); that is a field, not a kind.
    kind = "" if "=" in tokens[0] else tokens[0]
    fields = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return {"epoch": epoch, "kind": kind, "fields": fields}


def parse_number(text: str | None) -> float | None:
    """A finite decimal number, or None."""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
