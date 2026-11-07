#!/usr/bin/env python3
"""Household text adapter: the only household file that parses labels, times, JSON, phrases and argv.

Approved boundary (listed in parsing-policy.json). Consumers such as household_responder.py and
household_validate.py call these functions and never parse text themselves. Malformed input returns
None, or raises the same exception the stdlib would, so callers keep their UNKNOWN handling.

Input : labels.tsv rows (UTC stamp, tab, key=value fields), stated blind spans (HH:MMZ),
        ISO timestamps, JSON lines, transcript text from audio_hint, and CLI argv.
Output: typed values. Nothing here decides a state; it only reads one.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import time

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
ASK_TIME_RE = re.compile(r"который час|сколько (времени|часов)|what time|time is it", re.I)
ASK_WHERE_RE = re.compile(
    r"где (ты|ilya|илья|розалия|rozalia|бруно|bruno)|where (are|is)|кто (дома|здесь)|who('s| is) (home|here)",
    re.I,
)
GREET_RE = re.compile(r"^\W*(привет|здравствуй|hi|hello|hey)\b", re.I)
SMALL_TALK_RE = re.compile(r"как дела|how are you", re.I)
OCCUPIED_RE = re.compile(r"\b(?:ilya|rozalia)=living[_ ]room")
EMPTY_RE = re.compile(r"living_room=empty")
AWAY_RE = re.compile(r"\bilya=(?:outdoors|bedroom|balcony)")
ROZ_BED_RE = re.compile(r"\brozalia=bedroom")


# --- plain text ----------------------------------------------------------------------------------

def tidy(text: str) -> str:
    """Strip surrounding whitespace."""
    return text.strip()


def nonblank_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def epoch_text(text: str) -> float | None:
    """A plain epoch number from a file or command output, or None."""
    try:
        return float(text.strip())
    except ValueError:
        return None


def doc_summary(doc: str) -> str:
    """Second line of a module docstring, used as the argparse description."""
    return doc.split("\n")[1]


def parse_argv(parser, argv):
    return parser.parse_args(argv)


# --- JSON ----------------------------------------------------------------------------------------

def parse_json(text: str):
    return json.loads(text)


def record_time(line: str) -> float:
    """The 't' field of one JSON line. Raises ValueError or KeyError when absent."""
    return float(json.loads(line)["t"])


# --- times ---------------------------------------------------------------------------------------

def utc_epoch(stamp: str) -> float | None:
    """'2026-11-07T09:05:04Z' -> epoch seconds, or None when the stamp is not exactly that form."""
    try:
        return float(calendar.timegm(time.strptime(stamp, STAMP_FORMAT)))
    except ValueError:
        return None


def label_epoch(stamp: str) -> float | None:
    """Epoch of a label stamp; only the first 19 characters (YYYY-MM-DDTHH:MM:SS) are read."""
    try:
        return float(calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def iso_epoch(text) -> float | None:
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def stamp_utc(epoch: float) -> str:
    """Epoch seconds -> 'HH:MM' UTC."""
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%H:%M")


def blind_span_end(to_utc: str, label_at: float) -> float | None:
    """End of a stated blind span 'HH:MMZ' on the label's own UTC day."""
    day = time.strftime("%Y-%m-%d", time.gmtime(label_at))
    return label_epoch(f"{day}T{to_utc.rstrip('Z')}:00Z")


# --- labels.tsv ----------------------------------------------------------------------------------

def split_label_row(row: str) -> tuple[str, str]:
    """'stamp<TAB>rest' -> (stamp, rest). A row with no tab gives an empty rest."""
    stamp, _, rest = row.partition("\t")
    return stamp, rest


def label_fields(rest: str) -> list[tuple[str, str]]:
    """Tab-separated key=value fields as (normalised key, value); keys lower-case, spaces as '_'."""
    out = []
    for field in rest.split("\t"):
        key, _, value = field.partition("=")
        out.append((key.strip().lower().replace(" ", "_"), value))
    return out


def parse_label(line: str) -> tuple[str, str] | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 2 or not parts[0].endswith("Z"):
        return None
    return parts[0], " ".join(parts[1:])


def parse_labels(text: str) -> list[tuple[str, str]]:
    return [p for p in (parse_label(x) for x in text.splitlines()) if p]


def label_truth(text: str) -> bool | None:
    """True occupied, False empty of humans, None when the explicit rules do not decide."""
    occupied = bool(OCCUPIED_RE.search(text))
    empty = bool(EMPTY_RE.search(text)) or (bool(AWAY_RE.search(text)) and bool(ROZ_BED_RE.search(text)))
    if occupied and not empty:
        return True
    if empty and not occupied:
        return False
    return None


# --- spoken-reply phrase matching ----------------------------------------------------------------

def asks_time(text: str) -> bool:
    return bool(ASK_TIME_RE.search(text))


def asks_where(text: str) -> bool:
    return bool(ASK_WHERE_RE.search(text))


def greets(text: str) -> bool:
    return bool(GREET_RE.search(text))


def small_talk(text: str) -> bool:
    return bool(SMALL_TALK_RE.search(text))
