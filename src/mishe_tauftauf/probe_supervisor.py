"""Linux helper: bound a probe's process group even if its caller is killed."""
from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--hold-fd", type=int, action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not 0 < args.timeout <= 300:
        parser.error("bounded command required")
    cancelled = False

    def cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    # Install the handler before requesting the parent-death signal, then
    # check the parent again to close the race if it died before prctl.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "parent-death supervision unavailable")
    if os.getppid() != args.parent:
        return 124
    deadline = time.monotonic() + args.timeout
    child = None
    try:
        if cancelled:
            return 124
        child = subprocess.Popen(command, start_new_session=True, pass_fds=tuple(args.hold_fd))
        while child.poll() is None and not cancelled and time.monotonic() < deadline:
            time.sleep(.02)
        failed = cancelled or child.poll() is None
    finally:
        # The child's separate group includes ordinary grandchildren. Clean it
        # after natural exit too: an exited direct child may leave pipe owners.
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=1)
    return 124 if failed else child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
