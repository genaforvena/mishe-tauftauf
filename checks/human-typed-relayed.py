#!/usr/bin/env python3
"""Duty human-typed-relayed: the human's typed words in his interactive session
transcripts reach the operator chat.

The 2026-11-19 Sonnet-ban lift arrived only as a typed line in the Claude Code
transcripts (~/.claude/projects/<repo-slug>/*.jsonl); no canonical log carried
it, so the authority was invisible to every mind until traced by hand, and the
commit that acted on it mislabelled the source. The voice channel has
checks/human-inbound-relayed.py for exactly this failure; this piece is the
same relay for the typed channel: per-file byte cursors under
<home>/human-typed/cursor.json, each new human-typed line becomes a DM to the
operator, marked as the human's words.

It never sends anything outward and reads no token. The transcripts are the
human's own words to Mishe in this repository's sessions; the relay copies them
only into the gitignored site home, never into the repository.

Modes:
  (default)   dry run: report what would be relayed and the lag. Registration
              writes are still made: a transcript seen for the first time is
              registered at its end (history is never replayed), and a cursor
              past a truncated file is reset to the new end. Cursor writes
              merge forward of a concurrent relay.
  --require-timer UNIT   also check that UNIT is active (the relay timer); RED
                         when it is not, so a missing or stopped relay is
                         visible even before a human line goes stale

Exit: 0 GREEN (no unrelayed human line older than the limit),
      2 RED (a human line older than --limit minutes is not relayed, the
      relay stopped mid-file, or --require-timer is inactive),
      3 UNKNOWN (transcripts unreadable, cursor unusable, or the timer state
      cannot be read).
"""
import argparse
import calendar
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIMIT_MIN = 10
DM_TIMEOUT_S = 60


def relay_timer_state(unit):
    """Return (state, error) for a systemd user timer from systemctl is-active.

    ``state`` is the last line of stdout (active, inactive, failed, ...).
    ``error`` is set only when systemctl cannot be consulted at all, which the
    caller maps to UNKNOWN rather than treating an unreadable timer as RED.
    """
    try:
        res = subprocess.run(["systemctl", "--user", "is-active", unit],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    lines = res.stdout.strip().splitlines()
    if not lines:
        return None, res.stderr.strip() or "systemctl gave no state"
    return lines[-1].strip(), None


def slug_for_repo(repo):
    """The ~/.claude/projects directory name for sessions run in this repo."""
    rel = str(Path(repo).resolve()).lstrip("/")
    return "-" + rel.replace("/", "-")


def default_sessions(repo):
    return Path.home() / ".claude" / "projects" / slug_for_repo(repo)


def parse_at(stamp):
    return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))


def read_cursors(path):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, int) for k, v in data.items()):
        raise ValueError("cursor map is not {filename: byte offset}")
    return data


def write_cursors(path, cursors, reset=()):
    """Persist cursors atomically, merging forward of a concurrent writer.

    The 120 s observer and the 300 s relay timer both write this file. An
    observer that read cursors before a new typed line arrived must not rewind
    the relay's advance when its stale map lands last.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            try:
                current = read_cursors(path)
            except (OSError, ValueError):
                current = None
            merged = dict(current or {})
            for name, off in cursors.items():
                if name in reset or name not in merged or off > merged[name]:
                    merged[name] = off
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, sort_keys=True) + "\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def extract_text(content):
    """The typed text of a user message: a string, or text blocks of a list."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


def human_lines(data, start):
    """Yield (offset_after, at, stamp, text) for each new human-typed line.

    Only complete \\n-terminated lines are yielded, so a partially written
    transcript tail is left for the next run.
    """
    pos = start
    for raw in data[start:].splitlines(keepends=True):
        pos += len(raw)
        if not raw.endswith(b"\n"):
            break
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if (entry.get("type") == "user"
                and isinstance(entry.get("origin"), dict)
                and entry["origin"].get("kind") == "human"
                and entry.get("turnOrigin") == "human"
                and entry.get("promptSource") == "typed"
                and not entry.get("isMeta")
                and not entry.get("isSidechain")):
            text = extract_text((entry.get("message") or {}).get("content"))
            stamp = entry.get("timestamp") or ""
            if text and stamp:
                try:
                    yield pos, parse_at(stamp), stamp[:19] + "Z", text
                except ValueError:
                    continue


def append_dm(text, mishe_bin):
    cmd = [mishe_bin, "wall", "dm", "--source", "operator", "--to", "operator", "--text", text]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=DM_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"mishe wall dm timed out after {DM_TIMEOUT_S} s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"mishe wall dm could not run: {exc}") from exc
    if res.returncode != 0:
        raise RuntimeError(f"mishe wall dm failed: {res.stderr.strip() or res.stdout.strip()}")


def main(argv=None, now=None, mishe_bin=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, required=True, help="Mishe site home (cursor lives here)")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="the repository the transcripts belong to (default: this checkout)")
    ap.add_argument("--sessions", type=Path, default=None,
                    help="transcripts directory (default: ~/.claude/projects/<repo-slug>)")
    ap.add_argument("--limit", type=int, default=LIMIT_MIN, help="minutes before an unrelayed line is RED")
    ap.add_argument("--relay", action="store_true", help="write the DMs and advance the cursors")
    ap.add_argument("--require-timer", metavar="UNIT", default=None,
                    help="RED when this systemd user timer is not active (relay liveness)")
    a = ap.parse_args(argv)
    now = time.time() if now is None else now

    timer_state = None
    if a.require_timer:
        timer_state, timer_error = relay_timer_state(a.require_timer)
        if timer_state is None:
            print(f"human-typed-relayed: UNKNOWN cannot read relay timer {a.require_timer}: {timer_error}",
                  file=sys.stderr)
            return 3

    sdir = a.sessions if a.sessions is not None else default_sessions(a.repo)
    cursor_path = a.home / "human-typed" / "cursor.json"
    if not sdir.is_dir():
        print(f"human-typed-relayed: UNKNOWN transcripts directory missing: {sdir}", file=sys.stderr)
        return 3

    try:
        files = sorted(sdir.glob("*.jsonl"))
    except OSError as exc:
        print(f"human-typed-relayed: UNKNOWN transcripts unreadable: {exc}", file=sys.stderr)
        return 3
    try:
        cursors = read_cursors(cursor_path)
    except (OSError, ValueError) as exc:
        print(f"human-typed-relayed: UNKNOWN cursor unusable: {exc}", file=sys.stderr)
        return 3
    first = cursors is None
    if first:
        cursors = {}
    registered = []
    reset = []
    for f in files:
        try:
            size = f.stat().st_size
        except OSError as exc:
            print(f"human-typed-relayed: UNKNOWN cannot stat {f.name}: {exc}", file=sys.stderr)
            return 3
        off = cursors.get(f.name)
        if off is None:
            cursors[f.name] = size
            registered.append(f.name)
        elif off > size:
            print(f"human-typed-relayed: cursor past end of {f.name} (session rewritten); "
                  "reset to the new end, lines in the gap are lost", file=sys.stderr)
            cursors[f.name] = size
            reset.append(f.name)
    if registered or first or reset:
        try:
            write_cursors(cursor_path, cursors, reset=reset)
        except OSError as exc:
            print(f"human-typed-relayed: UNKNOWN cannot write cursor: {exc}", file=sys.stderr)
            return 3

    consumed = False
    pending = []
    for f in files:
        off = cursors[f.name]
        try:
            with f.open("rb") as fh:
                fh.seek(off)
                data = fh.read()
        except OSError as exc:
            print(f"human-typed-relayed: UNKNOWN cannot read {f.name}: {exc}", file=sys.stderr)
            return 3
        sess = f.stem[:8]
        for end, at, stamp, text in human_lines(data, 0):
            pending.append((f.name, off + end, at, stamp, sess, text))
        if not pending or pending[-1][0] != f.name:
            # No human line in this file's new bytes: consume the complete
            # lines so they are not rescanned every run. A partially written
            # tail (no trailing newline) stays unconsumed for the next run.
            nl = data.rfind(b"\n")
            if nl >= 0:
                cursors[f.name] = off + nl + 1
                consumed = True

    note = ""
    if first:
        note = f" (first run: {len(files)} transcript(s) registered, history not replayed)"
    elif registered:
        note = f" (registered new transcript(s): {', '.join(registered)})"

    if a.relay:
        mishe_bin = mishe_bin or shutil.which("mishe") or str(a.home / "bin" / "mishe")
        relayed = 0
        try:
            for name, end, at, stamp, sess, text in pending:
                append_dm(f"[human typed {stamp} {sess}] {text}", mishe_bin)
                cursors[name] = end
                write_cursors(cursor_path, cursors, reset=reset)
                relayed += 1
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"human-typed-relayed: RED relay stopped after {relayed} of {len(pending)}: {exc}; "
                  "cursor kept at the last relayed line", file=sys.stderr)
            return 2
        if consumed and not pending:
            # Persist non-human bytes consumed when no human line was relayed.
            try:
                write_cursors(cursor_path, cursors, reset=reset)
            except OSError as exc:
                print(f"human-typed-relayed: UNKNOWN cannot write cursor: {exc}", file=sys.stderr)
                return 3
        if pending:
            # Consume any non-human bytes after the last relayed human line.
            write_cursors(cursor_path, cursors, reset=reset)
        print(f"human-typed-relayed: GREEN {relayed} human line(s) relayed to operator{note}")
        return 0

    if consumed and not pending:
        # Persist non-human bytes consumed; human lines stay pending for --relay.
        try:
            write_cursors(cursor_path, cursors, reset=reset)
        except OSError as exc:
            print(f"human-typed-relayed: UNKNOWN cannot write cursor: {exc}", file=sys.stderr)
            return 3
    stale = [at for _, _, at, _, _, _ in pending if now - at > a.limit * 60]
    detail = f"{len(pending)} unrelayed human line(s)"
    if a.require_timer and timer_state != "active":
        print(f"human-typed-relayed: RED relay timer {a.require_timer} is {timer_state}; {detail}",
              file=sys.stderr)
        return 2
    if pending:
        detail += f", newest {int((now - pending[-1][2]) / 60)} min old"
    detail += note
    if stale:
        print(f"human-typed-relayed: RED {detail}; oldest is past {a.limit} min, run with --relay", file=sys.stderr)
        return 2
    print(f"human-typed-relayed: GREEN {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
