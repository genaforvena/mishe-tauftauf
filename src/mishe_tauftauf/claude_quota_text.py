"""Literal boundary for Claude Code's native, zero-inference `/usage` report.

The native display rounds percentages and reset times. Keep its precision and
source explicit; never substitute a model answer or a statusLine render time.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

WINDOWS = {"Current session": ("five_hour", 5 * 3600),
           "Current week (all models)": ("seven_day", 7 * 86400)}
MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
LINE = re.compile(r"(?P<label>Current session|Current week \(all models\)): "
                  r"(?P<used>\d{1,3})% used · resets (?P<month>[A-Z][a-z]{2}) "
                  r"(?P<day>\d{1,2}), (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
                  r"(?P<period>am|pm) \(UTC\)")


def read_usage(raw, *, now):
    row = json.loads(raw)
    if (not isinstance(row, dict) or row.get("type") != "result" or
            row.get("subtype") != "success" or row.get("is_error") is not False or
            row.get("local_command") != "usage" or row.get("modelUsage") != {}):
        raise ValueError("not a successful native usage report")
    for key in ("duration_api_ms", "num_turns", "total_cost_usd"):
        if type(row.get(key)) not in (int, float) or row[key] != 0:
            raise ValueError("quota command performed inference or lacks zero-inference evidence")
    usage = row.get("usage")
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        if not isinstance(usage, dict) or type(usage.get(key)) is not int or usage[key] != 0:
            raise ValueError("quota command lacks zero-token evidence")
    if not isinstance(row.get("result"), str):
        raise ValueError("native usage text missing")
    result = dict(state="FRESH", source="claude-native-usage", observed_at=now,
                  reset_precision_seconds=60, usage_precision_percentage_points=1)
    year = datetime.fromtimestamp(now, timezone.utc).year
    for line in row["result"].splitlines():
        if not any(line.startswith(label + ":") for label in WINDOWS):
            continue
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError("unsupported native quota window format")
        v = match.groupdict(); key, duration = WINDOWS[v["label"]]
        if key in result or int(v["used"]) > 100 or v["month"] not in MONTHS:
            raise ValueError("duplicate or malformed quota window")
        hour, minute = int(v["hour"]), int(v["minute"] or 0)
        if not 1 <= hour <= 12 or not 0 <= minute < 60:
            raise ValueError("invalid quota reset time")
        hour = hour % 12 + (12 if v["period"] == "pm" else 0)
        candidates = []
        for y in (year, year + 1):
            try:
                reset = datetime(y, MONTHS[v["month"]], int(v["day"]), hour, minute,
                                 tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            if now < reset <= now + duration + 60:
                candidates.append(reset)
        if len(candidates) != 1:
            raise ValueError("quota reset is expired or outside its window")
        result[key] = dict(used_percentage=int(v["used"]), resets_at=candidates[0], observed_at=now)
    if any(key not in result for key, _ in WINDOWS.values()):
        raise ValueError("both native quota windows are required")
    return result


def append_history(path, row):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(at=row["observed_at"], **{
            k: [row[k]["used_percentage"], row[k]["resets_at"]]
            for k, _ in WINDOWS.values()})) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args()
    from .claude_quota import collect
    result = collect(args.home)
    print(result["state"] + ": " + result.get("error", result["source"]))
    return 0 if result["state"] == "FRESH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
