"""Boundary adapter: decode the unit PSI sampler's JSONL bytes into records.

The reader (unit_psi_reading) owns the file I/O and the validity rules; this
module owns the text decoding only, so parsing stays inside an approved
boundary (see parsing-policy.json).
"""
import json


def parse_systemd_show(raw: str) -> dict[str, str]:
    """Decode ``systemctl show --property=...`` text ``KEY=VALUE`` lines.

    Returns a dict of properties found. Malformed or blank lines are ignored.
    Parsing lives here so callers do not open new parsing sites.
    """
    props: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            props[key] = value
    return props


def parse_jsonl_records(raw: bytes) -> tuple[list[dict], int]:
    """Parse sampler lines into records that carry a numeric 'at' and a dict 'cgroups'.

    Returns (records, malformed). Blank lines are ignored; anything else that is
    not such a record is counted as malformed, never silently dropped.
    """
    records: list[dict] = []
    malformed = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            malformed += 1
            continue
        if isinstance(record, dict) and isinstance(record.get("at"), (int, float)) \
                and isinstance(record.get("cgroups"), dict):
            records.append(record)
        else:
            malformed += 1
    return records, malformed
