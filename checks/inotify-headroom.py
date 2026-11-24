#!/usr/bin/env python3
"""Detector: the current user's inotify instance headroom.

On 2026-11-24 the user systemd manager held about 300 to 320 inotify instances
(one per path unit) against fs.inotify.max_user_instances=512. A new watcher then
fails with EMFILE, and the plant's revive service failed with "Too many open
files" before any signal went RED. This check counts every inotify instance the
current uid holds (from /proc/<pid>/fd) and compares it with the kernel limit.

Exit status:
  0  used is below WARN_FRACTION of the limit
  2  used is at or above WARN_FRACTION of the limit (headroom is gone)
  3  undecidable: the limit or the process table cannot be read
"""
from __future__ import annotations

import argparse
import os
import sys

WARN_FRACTION = 0.85
LIMIT_PATH = "/proc/sys/fs/inotify/max_user_instances"


def read_limit(path: str = LIMIT_PATH) -> int | None:
    try:
        with open(path) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def count_instances(proc: str = "/proc") -> tuple[int, int]:
    """Return (inotify instances held by this uid, processes that could not be read)."""
    uid = os.getuid()
    total = 0
    unreadable = 0
    try:
        entries = os.listdir(proc)
    except OSError:
        return 0, -1
    for entry in entries:
        if not entry.isdigit():
            continue
        base = os.path.join(proc, entry)
        try:
            if os.stat(base).st_uid != uid:
                continue
            fds = os.listdir(os.path.join(base, "fd"))
        except OSError:
            # The process exited between listdir and stat, or is not ours to read.
            unreadable += 1
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(base, "fd", fd))
            except OSError:
                continue
            if target == "anon_inode:inotify":
                total += 1
    return total, unreadable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="override the kernel limit (used to exercise the RED path)")
    parser.add_argument("--warn", type=float, default=WARN_FRACTION,
                        help="fraction of the limit at which the signal goes RED")
    args = parser.parse_args(argv)
    limit = args.limit if args.limit is not None else read_limit()
    if not limit or limit <= 0:
        print("UNKNOWN: cannot read fs.inotify.max_user_instances")
        return 3
    used, unreadable = count_instances()
    if unreadable < 0:
        print("UNKNOWN: cannot list /proc")
        return 3
    fraction = used / limit
    line = (f"inotify instances {used} of {limit} ({fraction:.0%}); "
            f"warn at {args.warn:.0%}; processes unreadable {unreadable}")
    if fraction >= args.warn:
        print("FAIL: " + line + "; a new watcher can fail with EMFILE (2026-11-24 revive)")
        return 2
    print("OK: " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
